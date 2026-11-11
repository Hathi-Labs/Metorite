"""WS-STU I5 — the Knowledge link: its shape and the section reader (pure).

Spec: ``project-docs/specs/ai_studio.md`` §14, row I5. A field or a step cites
a section of a Knowledge page. The link stores a page id, an optional heading
and the title the author saw. This module holds the parts with no database:
the link shape, its validation, the section cut and the size cap.

The gateway reader, ``gateway/studio/knowledge_link.py``, reads the page
through the Knowledge door as the viewer. It calls ``unreadable`` when the
viewer cannot open the page. That result holds the stored title only. It does
not say if the page is gone or the viewer has no access.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

#: Most characters of section text that a read returns (the A2 string cap).
MAX_TEXT_CHARS = 20_000
MAX_TITLE_CHARS = 200
MAX_HEADING_CHARS = 200

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


class KnowledgeLink(BaseModel):
    """The stored link: ``{page_id, heading?, title}``."""

    model_config = ConfigDict(extra="forbid")

    page_id: str
    heading: str | None = None
    title: str

    @field_validator("page_id")
    @classmethod
    def _page_id(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("page_id must be a page id") from exc

    @field_validator("heading")
    @classmethod
    def _heading(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > MAX_HEADING_CHARS:
            raise ValueError(f"heading is over {MAX_HEADING_CHARS} characters")
        return value

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        if len(value) > MAX_TITLE_CHARS:
            raise ValueError(f"title is over {MAX_TITLE_CHARS} characters")
        return value


def parse_link(doc: Any) -> KnowledgeLink:
    """The link for *doc*. Raise ``ValueError`` with one sentence when it is bad."""
    if not isinstance(doc, dict):
        raise ValueError("a Knowledge link must be an object")
    try:
        return KnowledgeLink.model_validate(doc)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "link"
        raise ValueError(f"{where}: {first['msg']}") from None


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def extract_section(body_md: str, heading: str | None) -> str | None:
    """The text of the section under *heading*, or None when no heading matches.

    The section runs from the heading line to the next heading of the same or a
    higher level. Lines in a fenced code block never count as headings. With no
    *heading*, the whole body is the section. The first matching heading wins.
    """
    if heading is None:
        return body_md
    want = _norm(heading)
    lines = body_md.splitlines()
    fenced = False
    start: int | None = None
    level = 0
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _HEADING.match(line)
        if m is None:
            continue
        depth = len(m.group(1))
        if start is None:
            if _norm(m.group(2)) == want:
                start, level = i, depth
        elif depth <= level:
            return "\n".join(lines[start:i]).strip("\n")
    if start is None:
        return None
    return "\n".join(lines[start:]).strip("\n")


def cap_text(text: str) -> tuple[str, bool]:
    """``(text, truncated)``, with the text cut to ``MAX_TEXT_CHARS``."""
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


def readable_result(
    title: str, text: str | None, *, found: bool,
) -> dict[str, Any]:
    """The result for a viewer who can open the page."""
    capped, truncated = cap_text(text) if text is not None else (None, False)
    return {
        "readable": True, "title": title, "text": capped,
        "section_found": found, "truncated": truncated,
    }


def unreadable(link: KnowledgeLink) -> dict[str, Any]:
    """The result for a viewer who cannot open the page: the stored title only.

    The shape has the same keys as ``readable_result``, so a caller cannot tell
    the two causes (no access, page gone) apart.
    """
    return {
        "readable": False, "title": link.title, "text": None,
        "section_found": False, "truncated": False,
    }
