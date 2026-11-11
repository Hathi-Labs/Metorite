"""WS-STU I5 — read the section a Knowledge link cites, as the viewer.

Spec: ``project-docs/specs/ai_studio.md`` §14, row I5. The pure parts (link
shape, section cut, size cap) are in ``acb_common.studio_knowledge_link``.

The read goes through ``routes/knowledge/core.load_page``. That is the one
Knowledge visibility predicate (``visible_pages_clause``). This module adds no
second check and opens no connection: the caller passes the session from
``tenant_session()``, which binds the org from the session. A page of another
org, a hidden page and a missing page all give the same ``unreadable`` result.
No route calls it yet, because the step player (C16) does not exist.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext
from acb_common.studio_knowledge_link import (
    KnowledgeLink,
    extract_section,
    parse_link,
    readable_result,
    unreadable,
)

from gateway.routes.knowledge import core


async def read_link(
    db: Any, user: UserContext, link: KnowledgeLink | dict[str, Any],
) -> dict[str, Any]:
    """The title and the section text for *user*, or the stored title and no text.

    Raise ``ValueError`` only for a malformed link. Never return the text of a
    page that *user* cannot open.
    """
    parsed = link if isinstance(link, KnowledgeLink) else parse_link(link)
    page = await core.load_page(db, user, parsed.page_id)
    if page is None:
        return unreadable(parsed)
    text = extract_section(page.body_md or "", parsed.heading)
    return readable_result(page.title, text, found=text is not None)
