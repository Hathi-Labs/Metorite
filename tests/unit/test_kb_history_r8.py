"""R8 — the history of a Knowledge page on a real database (WS-61 KB-2g).

Spec: ``project-docs/specs/knowledge_base.md`` §21.7, the KB-2g row.

``GET /knowledge/pages/{id}/versions/{n}`` returns one old version with its
body. What this proves against a REAL Postgres, through the real
``/knowledge`` router, as the non-privileged role of the phase-4 rehearsal:

- a member reads version 2 and version 5 of a page, the two sides of a
  compare, and then restores version 2 through the revert route;
- a member who cannot see the page gets 404 on each of its versions. That
  includes a member of the same org with no grant, a member of another org,
  and a member who is not the author of a draft;
- a version from before the history floor of a moved page answers 404. The
  author reads the whole history again in their own ``me`` space.

⚠️ This file is the fence for the version read.
``tests/unit/test_kb_visibility_one_seam.py`` does not see a read of
``kb_page_versions``, so a route that skipped ``core.load_page`` would stay
green there and fail here.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _assert_non_priv,
    _call,
    _me_space,
    _ok,
    _principal,
    _space,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE


def _version_path(page_id: str, n: int) -> str:
    return f"/knowledge/pages/{page_id}/versions/{n}"


async def _version_status(p: Any, user: Any, page_id: str, n: int) -> int:
    return (await _call(p, user, "GET", _version_path(page_id, n))).status_code


async def test_a_member_compares_two_versions_and_restores_one(ns, app_engine) -> None:  # noqa: F811
    """The KB-2g done-when: compare version 2 with version 5, restore 2."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email, reader_email = _address("writer"), _address("reader")
    writer = _principal(writer_email, ns.org_a)
    reader = _principal(reader_email, ns.org_a)
    space = await _space(ns, admin, "Wiki", [
        {"subject": writer_email, "level": "write"},
        {"subject": reader_email, "level": "read"},
    ])
    page = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "Refunds", "body_md": "one"},
                     status=201)
    pid = page["id"]
    await _ok(ns, writer, "PATCH", f"/knowledge/pages/{pid}",
              {"title": "Refund policy", "body_md": "Refunds take seven days."})
    await _ok(ns, writer, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "three"})
    await _ok(ns, writer, "POST", f"/knowledge/pages/{pid}/publish")
    edited = await _ok(ns, writer, "PATCH", f"/knowledge/pages/{pid}",
                       {"body_md": "Refunds take ten days."},
                       headers={"X-Actor-Via": "chat:knowledge"})
    assert edited["version"] == 5

    two = await _ok(ns, writer, "GET", _version_path(pid, 2))
    five = await _ok(ns, writer, "GET", _version_path(pid, 5))
    assert set(two) == {"page_id", "version", "title", "body_md", "author_email",
                        "reason", "via", "created_at"}
    assert (two["version"], two["title"], two["body_md"], two["reason"], two["via"]) == (
        2, "Refund policy", "Refunds take seven days.", "edit", "web")
    assert (five["version"], five["body_md"], five["reason"], five["via"]) == (
        5, "Refunds take ten days.", "edit", "chat")
    assert two["author_email"] == writer_email and two["page_id"] == pid
    assert two["created_at"] and five["created_at"] >= two["created_at"]

    # A reader of the space reads the history too. A read needs no write.
    assert (await _ok(ns, reader, "GET", _version_path(pid, 2)))["body_md"] == (
        "Refunds take seven days.")
    # A number past the newest version is not there. Version 0 is not a number.
    assert await _version_status(ns, writer, pid, 6) == 404
    assert await _version_status(ns, writer, pid, 0) == 422

    # Restore version 2: the revert route writes it again as version 6.
    restored = await _ok(ns, writer, "POST", f"/knowledge/pages/{pid}/revert",
                         {"version": 2})
    assert restored["version"] == 6
    assert (restored["title"], restored["body_md"]) == (
        "Refund policy", "Refunds take seven days.")
    detail = await _ok(ns, reader, "GET", f"/knowledge/pages/{pid}")
    assert detail["body_md"] == "Refunds take seven days."
    assert [v["version"] for v in detail["versions"]] == [6, 5, 4, 3, 2, 1]
    six = await _ok(ns, reader, "GET", _version_path(pid, 6))
    assert (six["reason"], six["body_md"]) == ("revert", "Refunds take seven days.")
    # The versions after 2 stay in the history, unchanged.
    assert (await _ok(ns, reader, "GET", _version_path(pid, 5)))["body_md"] == (
        "Refunds take ten days.")


async def test_a_member_who_cannot_see_the_page_gets_404(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin_a = _principal(_address("admin-a"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    outsider = _principal(_address("outsider"), ns.org_a)
    admin_b = _principal(_address("admin-b"), ns.org_b, CAPABILITY)
    ops = await _space(ns, admin_a, "Ops", [{"subject": writer_email, "level": "write"}])

    published = await _ok(ns, writer, "POST", "/knowledge/pages",
                          {"space_id": ops, "title": "Runbook", "body_md": "secret steps"},
                          status=201)
    pid = published["id"]
    await _ok(ns, writer, "POST", f"/knowledge/pages/{pid}/publish")
    draft = await _ok(ns, writer, "POST", "/knowledge/pages",
                      {"space_id": ops, "title": "Draft", "body_md": "not yet"},
                      status=201)

    # Not vacuous: the author reads both.
    assert await _version_status(ns, writer, pid, 1) == 200
    assert await _version_status(ns, writer, draft["id"], 1) == 200

    # A member of the same org with no grant on the space.
    for n in (1, 2):
        assert await _version_status(ns, outsider, pid, n) == 404
    # A member of another org, even with knowledge:manage there.
    assert await _version_status(ns, admin_b, pid, 1) == 404
    # knowledge:manage reads the published page, never another author's draft.
    assert await _version_status(ns, admin_a, pid, 1) == 200
    assert await _version_status(ns, admin_a, draft["id"], 1) == 404
    # A page id that is not a UUID is not there either.
    assert await _version_status(ns, writer, "not-a-page", 1) == 404

    # A revoked grant: the same writer, after the grant goes, sees no version.
    await _ok(ns, admin_a, "PUT", f"/knowledge/spaces/{ops}/grants", {"grants": []})
    assert await _version_status(ns, writer, pid, 1) == 404


async def test_a_version_under_the_floor_of_a_moved_page_is_refused(ns, app_engine) -> None:  # noqa: F811
    """A page written in a ``me`` space and then shared does not show its
    private versions, through this read as through revert."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, writer_email = _address("author"), _address("writer")
    author = _principal(author_email, ns.org_a)
    writer = _principal(writer_email, ns.org_a)
    eng = await _space(ns, admin, "Eng", [
        {"subject": author_email, "level": "write"},
        {"subject": writer_email, "level": "write"},
    ])
    page = await _ok(ns, author, "POST", "/knowledge/pages",
                     {"title": "Plan", "body_md": "private salary notes"}, status=201)
    pid = page["id"]
    await _ok(ns, author, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "the plan"})
    moved = await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": eng})
    assert moved["version"] == 3 and moved["history_from"] == 3
    await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/publish")

    for reader in (writer, author):
        assert await _version_status(ns, reader, pid, 1) == 404
        assert await _version_status(ns, reader, pid, 2) == 404
        at_floor = await _ok(ns, reader, "GET", _version_path(pid, 3))
        assert (at_floor["reason"], at_floor["body_md"]) == ("move", "the plan")
        assert await _version_status(ns, reader, pid, 4) == 200

    # Back in the author's own `me` space, the author reads the whole history.
    home = await _me_space(ns, author)
    await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": home})
    first = await _ok(ns, author, "GET", _version_path(pid, 1))
    assert first["body_md"] == "private salary notes"
    # The writer of the old space sees the page no more at all.
    assert await _version_status(ns, writer, pid, 3) == 404


async def test_a_version_past_int4_is_refused_not_a_500(ns, app_engine) -> None:  # noqa: F811
    """A number past the int4 column refuses at the route, never in asyncpg.

    Before the bound, 2147483648 reached ``kb_page_versions.version`` as a
    bind and the read and the revert both answered 500.
    """
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Wiki", [{"subject": writer_email, "level": "write"}])
    page = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "Refunds", "body_md": "one"},
                     status=201)
    pid = page["id"]
    too_big = 2_147_483_648
    assert await _version_status(ns, writer, pid, too_big) == 422
    assert await _version_status(ns, writer, pid, -1) == 422
    # The top of int4 is a valid number, and no such version exists.
    assert await _version_status(ns, writer, pid, 2_147_483_647) == 404
    revert = await _call(ns, writer, "POST", f"/knowledge/pages/{pid}/revert",
                         {"version": too_big})
    assert revert.status_code == 422
    # Not vacuous: the page and its version 1 are there.
    assert await _version_status(ns, writer, pid, 1) == 200
