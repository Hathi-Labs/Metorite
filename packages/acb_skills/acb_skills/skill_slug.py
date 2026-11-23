"""The ONE slug of a Studio skill name. Pure. The route, the store and the
SKILL.md export all read it here."""

from __future__ import annotations

import re
import unicodedata

__all__ = ["MAX_SLUG_CHARS", "slug_from_name"]

#: The bound of the CHECK ``studio_skills_slug``.
MAX_SLUG_CHARS = 64
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug_from_name(name: str) -> str:
    """The slug of *name*: lower case, a hyphen between the parts, at most
    ``MAX_SLUG_CHARS`` characters. An accent drops to its letter. A name with
    no ASCII letter or digit gives the empty string."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _NOT_SLUG.sub("-", folded.lower()).strip("-")
    return slug[:MAX_SLUG_CHARS].rstrip("-")
