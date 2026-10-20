"""The files of a mail, as the reply drafter reads them (owner, 2026-10-20).

The drafter saw only the NAMES of the files of a mail. So a reply to "see the
attached spec" could not use the spec, and a drafter that knew a file was
there and could not see it wrote "I could not open your attachment" to the
customer. The member can open every file, so that sentence is false, and it
goes out in the member's name.

This module gives the drafter what it can read, and names what it did not:

* **A file with text** (PDF, Word, Excel, PowerPoint, text and the rest of
  ``acb_skills.attachment_text``) goes in as text. The read is the text route
  of the Email app (``transport.attachments.attachment_text``), so the
  ownership check, the cache and the parse limits are that route's.
* **An image** goes in as an image only when the switch
  ``email_draft_reads_images`` is on AND the AI Router serves the call. The
  call then declares ``task: "vision"``, and the Router picks a model that
  reads images (D-AI-2). With the switch off, the image is named as not read.
  A small image is a logo in a signature, and it is left out.
* **What the drafter did not read** is a list. The drafter is told never to
  write that it cannot open a file, and the member's reply card names each
  file, so the member checks the reply against it.

Each read has a time budget, so a slow parse never holds the member's card.
Fence: ``tests/unit/test_email_draft_files.py``.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext
from acb_common import get_logger, get_settings
from sqlalchemy import text

_log = get_logger("gateway.email.draft_files")

#: The most files of one mail that the drafter reads.
MAX_FILES = 6
#: The characters of one file that the drafter sees.
MAX_FILE_CHARS = 10_000
#: The characters of all files together.
MAX_TOTAL_CHARS = 30_000
#: The seconds that all reads of one draft may take.
READ_BUDGET_S = 12.0
#: An image smaller than this is a logo or an icon in a signature.
MIN_IMAGE_BYTES = 20 * 1024
#: The largest image that goes to the model.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
#: The most images that go to the model.
MAX_IMAGES = 3
#: The image types that a vision model takes.
_VISION_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: Why a file was not read, in words for the member's card.
UNREAD_IMAGE = "an image"
UNREAD_NO_TEXT = "no text that can be read"
UNREAD_KIND = "a type that cannot be read"
UNREAD_SLOW = "not read in time"
UNREAD_LIMIT = "over the limit of files"


@dataclass
class DraftFiles:
    """What the drafter read of the files of one mail, and what it did not."""

    #: ``(file name, text, cut)`` for each file read as text.
    texts: list[tuple[str, str, bool]] = field(default_factory=list)
    #: ``(file name, data URL)`` for each image that goes to the model.
    images: list[tuple[str, str]] = field(default_factory=list)
    #: ``(file name, why)`` for each file that the drafter did not read.
    unread: list[tuple[str, str]] = field(default_factory=list)

    def read_names(self) -> list[str]:
        return [n for n, _t, _c in self.texts] + [n for n, _u in self.images]

    def notices(self) -> list[str]:
        """The lines of the member's reply card. A file the drafter did not
        read is named, so the member checks the reply against it."""
        out: list[str] = []
        if self.unread:
            names = ", ".join(f"{n} ({why})" for n, why in self.unread)
            out.append(f"The assistant did not read {names}. Check the reply "
                       "against it before you send.")
        return out


def images_on() -> bool:
    """True when a draft may send images to the model.

    Two halves, and both must hold. The switch is the owner's call. The
    Router must serve the call, because only the Router turns
    ``task: "vision"`` into a model that reads images. A direct call goes to
    the tier's chat model, which can be blind, and a blind model answers
    about a picture it never saw (``customer_console.router``, D16)."""
    if not getattr(get_settings(), "email_draft_reads_images", False):
        return False
    try:
        from acb_llm.routed import routing_is_on  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(routing_is_on())
    except Exception:  # noqa: BLE001
        return False


def _suffix(name: str, mime: str) -> str:
    from gateway.routes.email.transport.attachments import (  # noqa: PLC0415
        _SUFFIX_OF_MIME,
        _mime_base,
        _name_suffix,
    )

    return _name_suffix(name) or _SUFFIX_OF_MIME.get(_mime_base(mime), "")


def _is_image(name: str, mime: str) -> bool:
    from gateway.routes.email.transport.attachments import (  # noqa: PLC0415
        _IMAGE_SUFFIXES,
        _mime_base,
        _name_suffix,
    )

    named = _name_suffix(name)
    return named in _IMAGE_SUFFIXES or (not named and _mime_base(mime).startswith("image/"))


async def _list_files(db: Any, message_id: str) -> list[Any]:
    """The files of the mail, oldest name first. The caller proved that the
    member owns the mail, and each read proves it again."""
    try:
        return list((await db.execute(text(
            "SELECT id, filename, mime_type, size_bytes FROM email_attachments "
            "WHERE message_id = CAST(:mid AS uuid) ORDER BY filename"
        ), {"mid": str(message_id)})).fetchall())
    except Exception as exc:  # noqa: BLE001 - a draft still writes
        _log.warning("email.draft_files_list_failed", error=type(exc).__name__)
        return []


async def _read_text(aid: str, user: UserContext) -> Any:
    from gateway.routes.email.transport.attachments import attachment_text  # noqa: PLC0415

    return await attachment_text(aid, user)


async def _read_image(aid: str, user: UserContext, mime: str) -> str | None:
    """A data URL of the image, or None when it cannot go to the model."""
    from gateway.routes.email.core import _tenant_session  # noqa: PLC0415
    from gateway.routes.email.transport.attachments import (  # noqa: PLC0415
        _fetch_owned_attachment,
    )

    async with _tenant_session() as db:
        got = await _fetch_owned_attachment(db, aid, user, max_bytes=MAX_IMAGE_BYTES)
    if not got.content or len(got.content) > MAX_IMAGE_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(got.content).decode('ascii')}"


async def load_draft_files(
    db: Any, message_id: str, user: UserContext | None, *,
    budget_s: float = READ_BUDGET_S, vision: bool | None = None,
) -> DraftFiles:
    """Read the files of *message_id* for the drafter, within *budget_s*.

    With no *user* (a rule draft runs with no member session) nothing is
    read, and each file is named as not read. Never raises: a draft writes
    whatever happens here."""
    out = DraftFiles()
    rows = await _list_files(db, message_id)
    if not rows:
        return out
    see_images = images_on() if vision is None else vision
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    total = 0
    for i, row in enumerate(rows):
        name = str(getattr(row, "filename", "") or "file").strip() or "file"
        mime = str(getattr(row, "mime_type", "") or "")
        size = int(getattr(row, "size_bytes", 0) or 0)
        aid = str(getattr(row, "id", ""))
        image = _is_image(name, mime)
        if image and 0 < size < MIN_IMAGE_BYTES:
            continue  # a logo in a signature: never named, never read
        if i >= MAX_FILES:
            out.unread.append((name, UNREAD_LIMIT))
            continue
        left = deadline - loop.time()
        if user is None or left <= 0:
            out.unread.append((name, UNREAD_IMAGE if image else UNREAD_SLOW))
            continue
        try:
            if image:
                vmime = _VISION_MIME.get(_suffix(name, mime))
                if not (see_images and vmime and len(out.images) < MAX_IMAGES
                        and size <= MAX_IMAGE_BYTES):
                    out.unread.append((name, UNREAD_IMAGE))
                    continue
                url = await asyncio.wait_for(_read_image(aid, user, vmime), timeout=left)
                if url:
                    out.images.append((name, url))
                else:
                    out.unread.append((name, UNREAD_IMAGE))
                continue
            answer = await asyncio.wait_for(_read_text(aid, user), timeout=left)
        except TimeoutError:
            out.unread.append((name, UNREAD_SLOW))
            continue
        except Exception as exc:  # noqa: BLE001 - one file never stops a draft
            _log.warning("email.draft_file_read_failed", error=type(exc).__name__)
            out.unread.append((name, UNREAD_KIND))
            continue
        body = str(getattr(answer, "text", "") or "").strip()
        if not body:
            kind = str(getattr(answer, "kind", "") or "")
            out.unread.append((name, UNREAD_NO_TEXT if kind == "no_text" else UNREAD_KIND))
            continue
        room = min(MAX_FILE_CHARS, MAX_TOTAL_CHARS - total)
        if room <= 0:
            out.unread.append((name, UNREAD_LIMIT))
            continue
        cut = len(body) > room or bool(getattr(answer, "truncated", False))
        out.texts.append((name, body[:room], cut))
        total += min(len(body), room)
    return out


# ── A drafter never tells the recipient what the AI could not do ─────────────
#
# The prompt forbids it (``NO_LIMIT_RULE``), and this check removes what a
# model writes anyway. It runs on the DRAFTER's text only. A text that the
# member wrote or agreed in the chat can rightly say "your file did not open",
# so the reply card never runs it on that text.

import re as _re  # noqa: E402

#: The rule that each reply drafter carries in its system prompt.
NO_LIMIT_RULE = (
    "- NEVER write that you cannot open, read, see, view, access or download a "
    "file, an attachment, an image, a picture or a link, and never ask the "
    "sender to send one again for that reason. You write as the account "
    "owner, who can open every file. Never say that the email looks cut off, "
    "truncated or incomplete. When the reply depends on a file whose text you "
    "were not given, answer the rest and leave that point open, without "
    "guessing what the file says.\n"
)

# "I cannot open the file", in its many forms: a claim of the reader, then
# what it could not do, then the file. "The image you mentioned was not
# attached" is NOT here: the drafter knows the list of files, so that can be
# true, and asking for a file the sender forgot is a fair reply.
_CANT = (
    r"(?:can(?:no|')t|could(?:n't| not)|unable to|not able to|"
    r"(?:was|were|have|has|had)(?:n't| not) (?:been )?able to|"
    r"(?:having|had|have) (?:some )?(?:trouble|difficulty|difficulties|issues?|problems?)|"
    r"failed to)"
)
_VERB = (r"(?:open(?:ing)?|read(?:ing)?|view(?:ing)?|see(?:ing)?|access(?:ing)?|"
         r"download(?:ing)?|load(?:ing)?|display(?:ing)?|render(?:ing)?|"
         r"retriev(?:e|ing)|process(?:ing)?|extract(?:ing)?)")
_OBJ = (r"(?:attach\w*|files?|images?|pictures?|photos?|pdfs?|documents?|"
        r"screenshots?|links?|spreadsheets?|enclosures?|scans?)")
# "The PDF did not come through", "the file will not open".
_FAIL = (r"(?:did(?:n't| not) come through|(?:is|was)(?:n't| not) coming through|"
         r"(?:won't|wouldn't|doesn't|does not|did not|didn't|will not|would not) "
         r"(?:open|load|display)|"
         r"(?:is|was|seems?|appears?)(?: to be)? (?:corrupt(?:ed)?|unreadable))")
_LIMIT_RE = _re.compile(
    rf"\b{_CANT}\b.*\b{_VERB}\b.*\b{_OBJ}\b|\b{_OBJ}\b.*\b{_CANT}\b.*\b{_VERB}\b"
    rf"|\b{_OBJ}\b.*\b{_FAIL}\b|\b{_FAIL}\b.*\b{_OBJ}\b",
    _re.IGNORECASE | _re.DOTALL,
)
_CUT_RE = _re.compile(
    r"\b(?:email|e-mail|mail|message|note|text)\b.*\b(?:cut off|got cut|truncated|"
    r"incomplete|garbled|ended abruptly|ends abruptly|came through partially)\b",
    _re.IGNORECASE | _re.DOTALL,
)
_AI_RE = _re.compile(r"\bas an ai\b|\bi(?:'m| am) an ai\b|\blanguage model\b", _re.IGNORECASE)
#: The ask that follows a claim: "Could you resend it?". It goes with the
#: claim, and only right after one.
_RESEND_RE = _re.compile(
    r"\b(?:re-?send|send (?:it|them|this|that|the \w+)(?: \w+)? again|"
    r"share the rest|send the rest|another copy|re-?attach|try (?:attaching|sending) again)\b",
    _re.IGNORECASE,
)
_SENTENCE_RE = _re.compile(r"(?<=[.!?])\s+")


def _is_limit_claim(sentence: str) -> bool:
    return bool(_LIMIT_RE.search(sentence) or _CUT_RE.search(sentence)
                or _AI_RE.search(sentence))


def strip_limit_claims(body: str) -> tuple[str, list[str]]:
    """*body* with each sentence that claims an AI limit removed, and those
    sentences. A request to resend that comes right after such a sentence
    goes with it. The first line, the greeting, is never touched. A
    paragraph that loses every sentence goes."""
    paragraphs = _re.split(r"\n\s*\n", body or "")
    removed: list[str] = []
    kept: list[str] = []
    after_claim = False
    for n, para in enumerate(paragraphs):
        if n == 0 and "\n" not in para.strip() and len(para) < 80:
            kept.append(para)  # the greeting line
            continue
        lines_out: list[str] = []
        for line in para.split("\n"):
            good: list[str] = []
            for part in _SENTENCE_RE.split(line):
                if _is_limit_claim(part) or (after_claim and _RESEND_RE.search(part)):
                    removed.append(part.strip())
                    after_claim = True
                    continue
                if part.strip():
                    after_claim = False
                good.append(part)
            if good or not line.strip():
                lines_out.append(" ".join(good))
        text_out = "\n".join(lines_out).strip()
        if text_out:
            kept.append(text_out)
    if not removed:
        return body, []
    return "\n\n".join(kept).strip(), removed
