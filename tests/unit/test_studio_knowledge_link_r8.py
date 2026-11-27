"""WS-STU I5 — the Knowledge link on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14, row I5. Code:
``gateway/studio/knowledge_link.py``. Hermetic twin:
``test_studio_knowledge_link.py``.

Two organizations, as the non-privileged role, so FORCE RLS binds. The pages
are made through the real ``/knowledge`` router.

Acceptance: a member with access gets the section. A member without access
gets the stored title and no text. A page of the other org is unreadable. A
missing page and a hidden page give the same answer.

Run: ``eval "$(bash scripts/dev_db.sh --export)"`` and then
``uv run pytest tests/unit/test_studio_knowledge_link_r8.py -v -rs``.
A skip is not a pass.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _add_member,
    _address,
    _app_dsn,
    _assert_non_priv,
    _ok,
    _principal,
    ns,
)

pytestmark = _DB_GATE

BODY = "# Manual\n\nIntro.\n\n## Torque\n\nTighten to 12 Nm.\n\n## Safety\n\nWear gloves.\n"


async def _page(p: Any, admin: Any, space: str, title: str) -> str:
    page = await _ok(p, admin, "POST", "/knowledge/pages",
                     {"space_id": space, "title": title, "body_md": BODY}, status=201)
    await _ok(p, admin, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


async def _read(p: Any, user: Any, link: dict) -> dict:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.db import tenant_session
    from gateway.studio.knowledge_link import read_link

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            bind_tenant(user.organization_id)
            async with tenant_session() as db:
                return await read_link(db, user, link)
    finally:
        release_tenant(scope)


async def test_access_gets_the_section_and_no_access_gets_the_title_only(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    reader, outsider, member_b = _address("reader"), _address("outsider"), _address("b")
    _add_member(ns, ns.org_a, reader)
    _add_member(ns, ns.org_a, outsider)
    _add_member(ns, ns.org_b, member_b)
    admin_a = _principal(_address("admin-a"), ns.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), ns.org_b, CAPABILITY)

    space = (await _ok(ns, admin_a, "POST", "/knowledge/spaces",
                       {"name": "Assembly", "grants": [{"subject": reader, "level": "read"}]},
                       status=201))["id"]
    page = await _page(ns, admin_a, space, "Assembly manual")
    # Org B holds a page of its own, open to its whole org.
    hb = (await _ok(ns, admin_b, "POST", "/knowledge/spaces",
                    {"name": "Handbook", "grants": [{"subject": "org", "level": "read"}]},
                    status=201))["id"]
    await _page(ns, admin_b, hb, "B handbook")

    link = {"page_id": page, "heading": "Torque", "title": "Stored title"}

    ok = await _read(ns, _principal(reader, ns.org_a), link)
    assert ok["readable"] is True and ok["title"] == "Assembly manual"
    assert "Tighten to 12 Nm." in ok["text"] and "Wear gloves" not in ok["text"]

    whole = await _read(ns, _principal(reader, ns.org_a), {**link, "heading": None})
    assert "Wear gloves." in whole["text"]

    gap = await _read(ns, _principal(reader, ns.org_a), {**link, "heading": "Missing"})
    assert gap["readable"] is True and gap["text"] is None and gap["section_found"] is False

    denied = await _read(ns, _principal(outsider, ns.org_a), link)
    cross = await _read(ns, _principal(member_b, ns.org_b), link)
    gone = await _read(ns, _principal(reader, ns.org_a),
                       {**link, "page_id": str(uuid.uuid4())})
    expected = {"readable": False, "title": "Stored title", "text": None,
                "section_found": False, "truncated": False}
    assert denied == cross == gone == expected
    assert "Tighten" not in repr((denied, cross, gone))
