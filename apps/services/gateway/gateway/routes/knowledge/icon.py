"""The icon of a Knowledge page (WS-61 KB-2f).

Spec: ``project-docs/specs/knowledge_base.md`` §21.7, the KB-2f row.

A page icon is one emoji, or nothing. It is stored in ``kb_pages.icon``,
which a CHECK of migration 287 keeps to 16 characters. It is not the icon
NAME of a space (``kb_spaces.icon``, a Lucide name).

``PATCH /knowledge/pages/{id}`` takes ``icon``. A PATCH that changes ONLY the
icon goes through :func:`write_icon`. It writes no version, keeps
``updated_at`` and does not reindex, as a move of the place only does
(KB-2e), because an icon is not a change in substance
(``company_context.md`` §3). A PATCH that also changes the title or the body
puts the icon in the same versioned UPDATE as the rest.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from acb_auth import UserContext
from fastapi import HTTPException
from gateway.routes.knowledge import core
from sqlalchemy import text

#: The longest icon, in characters. The CHECK of migration 287 holds the same.
MAX_ICON = 16

#: The one emoji that starts with an ASCII mark: a keycap, such as "1️⃣".
_KEYCAP = re.compile("^[0-9#*]️?⃣$")

#: The Unicode categories that never belong in an emoji: control marks,
#: surrogates and every kind of space.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cs", "Zs", "Zl", "Zp"})


def clean_icon(raw: str | None) -> str | None:
    """The icon to store, or None to clear it. 422 on a value that is no emoji.

    An empty value clears the icon. The check is a sieve, not a full emoji
    parser. It refuses a letter of any script, a control mark, a space, and
    an ASCII mark outside a keycap, so a title or a tag cannot pass as an
    icon. The client draws the value as text, never as HTML.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > MAX_ICON:
        raise HTTPException(
            status_code=422, detail=f"An icon is one emoji, at most {MAX_ICON} characters.",
        )
    if _KEYCAP.match(value):
        return value
    for char in value:
        category = unicodedata.category(char)
        if char.isascii() or category in _REFUSED_CATEGORIES or category.startswith("L"):
            raise HTTPException(status_code=422, detail="An icon is one emoji.")
    return value


async def write_icon(
    db: Any, user: UserContext, page_id: str, icon: str | None,
) -> Any:
    """Set the icon of a page the caller holds locked, and return the new row.

    No ``updated_at``, no version and no reindex: the caller returns the row
    as it is. The statement names the org, so a session with row level
    security off still writes one organization only.
    """
    return (await db.execute(
        text("UPDATE kb_pages AS p SET icon = :icon "
             "WHERE p.organization_id = CAST(:org AS uuid) "
             f"AND p.id = CAST(:pid AS uuid) RETURNING {core.PAGE_COLUMNS}"),
        {"icon": icon, "org": core.org_of(user), "pid": page_id},
    )).first()
