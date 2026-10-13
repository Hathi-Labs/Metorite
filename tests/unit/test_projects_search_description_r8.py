"""Task search over descriptions, on a REAL Postgres, as the app role (R8).

Owner request, 2026-10-13. The Projects chat told the owner that a task's
description "is not in that index at all", and offered to open 120 tasks one
at a time. ``/projects/search`` matched descriptions all along, but as ONE
substring, and a hit carried no text to show why it came back. The route now
matches every word in the title or the description, ranks title matches
first, and cuts a short snippet for a description hit.

What only a database can prove, and so lives here and not in
``test_projects_search.py``:

* **the ranking.** The six tiers are a SQL ``CASE`` evaluated before the
  ``LIMIT``. A fake that ranks in Python agrees with any ``CASE``.
* **the snippet.** ``strpos``/``substr`` on the real column, through the
  ``LATERAL`` step, with the real character positions.
* **the scope.** The description arm runs inside the same WHERE as the
  tenant and the grant closure (``task_visibility_clause``), on a database
  promoted through all four phases, as a role that is not the owner and
  cannot bypass RLS. A member of org B never finds org A's task, and a member
  never finds another member's PRIVATE task through a description word.

Runs on the H3 rehearsal's ``promoted`` fixture: a dedicated sibling
database with the ladder and the four generated phases.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._projects_fakes import member_user
from tests.unit.test_h3_rls_promotion_rehearsal import (
    _DB_GATE,
    promoted,  # noqa: F401 — a fixture, used by name
)

pytestmark = _DB_GATE

ANA = "ana@descsearch.test"
BOB = "bob@descsearch.test"
CARA = "cara@descsearch-b.test"

#: Words no other seeded row holds, so a hit is unambiguous.
WORD = "zephyrquill"


def _project(c, *, org: str, name: str, owner: str | None = None,
             grant: str | None = "org") -> tuple[str, str]:
    """One project, its one lane, and one grant (``org`` or an email)."""
    project, lane = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses, personal_owner) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
        ":n, 'manual', :by, true, :own)"),
        {"p": project, "o": org, "n": name, "by": owner or ANA, "own": owner})
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, "
        "'todo', true)"), {"s": lane, "p": project})
    if grant:
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), :g, :by, CAST(:o AS uuid))"),
            {"p": project, "g": grant, "by": ANA, "o": org})
    return project, lane


def _task(c, *, org: str, project: str, lane: str, title: str, number: int,
          description: str | None = None, updated: str = "2026-10-01") -> str:
    task = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, description, source, created_by, task_number, "
        "updated_at) VALUES (CAST(:t AS uuid), CAST(:o AS uuid), "
        "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :ti, :d, "
        "'manual', :by, :n, CAST(:u AS timestamptz))"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title,
         "d": description, "by": ANA, "n": number, "u": updated})
    return task


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """Two orgs. Org A has a team project, Bob's private project, and Ana
    and Bob. Org B has Cara and a task that holds every searched word."""
    a, b = promoted.org_a, promoted.org_b
    ids: dict[str, str] = {}
    filler = "Nothing of note is written in this part of the text. " * 6
    with promoted.admin_engine.begin() as c:
        for email, org in ((ANA, a), (BOB, a), (CARA, b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        team, lane = _project(c, org=a, name="Ops")
        mine, plane = _project(c, org=a, name="Bob's area", owner=BOB, grant=BOB)
        other, olane = _project(c, org=b, name="B Ops")
        # A description hit only: the words sit apart, in the wrong order.
        ids["described"] = _task(
            c, org=a, project=team, lane=lane, title="Finance chores", number=1,
            description=filler + f"Waiting on the {WORD} approval of the invoice. " + filler,
            updated="2026-10-09")
        # A title hit, OLDER than the description hit, so rank beats recency.
        ids["titled"] = _task(
            c, org=a, project=team, lane=lane, title=f"{WORD.title()} invoice",
            number=2, updated="2026-01-01")
        # Bob's private task. Ana must never find it through a description.
        ids["private"] = _task(
            c, org=a, project=mine, lane=plane, title="Bob notes", number=3,
            description=f"My own {WORD} invoice notes.")
        # Org B. Nobody in org A may find it.
        ids["other_org"] = _task(
            c, org=b, project=other, lane=olane, title="B chores", number=1,
            description=f"The {WORD} invoice for B.")
    yield promoted, ids
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})


def _app_engine(ns: Any):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    return create_async_engine(
        ns.app_url.set(drivername="postgresql+asyncpg"), future=True, poolclass=NullPool
    )


async def _search(ns: Any, monkeypatch, *, who: str, org: str, q: str) -> dict:
    """``GET /projects/search`` end to end, as the app role, with the session
    bound to ``org`` the way the gateway binds ``app.tenant_id``."""
    pytest.importorskip("asyncpg")
    from gateway.routes.projects import search as pm_search

    eng = _app_engine(ns)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org}
            )
            yield conn

    monkeypatch.setattr(pm_search, "_tenant_session", _session)
    try:
        return await pm_search.search_tasks(q=q, user=member_user(who))
    finally:
        await eng.dispose()


def _ids(result: dict) -> list[str]:
    return [r["id"] for r in result["rows"]]


async def test_r8_a_word_only_in_a_description_finds_the_task(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q="approval")
    assert _ids(out) == [ids["described"]]


async def test_r8_words_apart_and_out_of_order_still_match(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a,
                        q=f"invoice {WORD} approval")
    assert _ids(out) == [ids["described"]]


async def test_r8_a_title_match_ranks_above_a_description_match(seeded, monkeypatch):
    """The title hit is nine months OLDER, so only the rank can put it first."""
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{WORD} invoice")
    assert _ids(out) == [ids["titled"], ids["described"]]
    ranks = [r["rank"] for r in out["rows"]]
    assert ranks[0] < 4 <= ranks[1]


async def test_r8_the_task_number_still_matches(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q="#2")
    assert _ids(out) == [ids["titled"]]
    assert out["rows"][0]["rank"] == 0


async def test_r8_org_b_never_finds_org_a_tasks(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=CARA, org=ns.org_b, q=WORD)
    assert _ids(out) == [ids["other_org"]]


async def test_r8_a_private_task_is_never_a_description_hit(seeded, monkeypatch):
    """Bob's private task holds the word in its DESCRIPTION. Ana holds no
    grant on Bob's area, so she never finds it. Bob does: the positive
    control, so the absence is the grant and not a broken seed."""
    ns, ids = seeded
    ana = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{WORD} notes")
    assert ids["private"] not in _ids(ana)
    bob = await _search(ns, monkeypatch, who=BOB, org=ns.org_a, q=f"{WORD} notes")
    assert _ids(bob) == [ids["private"]]


async def test_r8_the_snippet_holds_the_words_and_is_capped(seeded, monkeypatch):
    from gateway.routes.projects.search import SNIPPET_CHARS

    ns, _ids_unused = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q="approval")
    (hit,) = out["rows"]
    snippet = hit["snippet"]
    assert "approval of the invoice" in snippet
    assert len(snippet) <= SNIPPET_CHARS
    assert snippet.startswith("…") and snippet.endswith("…")
    assert "\n" not in snippet


async def test_r8_a_title_hit_carries_no_snippet(seeded, monkeypatch):
    ns, _ids_unused = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{WORD} invoice")
    assert out["rows"][0]["snippet"] is None


async def test_r8_the_list_endpoint_matches_words_in_the_description(
    seeded, monkeypatch,
):
    """The list endpoint's ``?q=`` runs the same word predicate, so the board
    filter and the chat's ``list_tasks`` agree with search."""
    pytest.importorskip("asyncpg")
    from gateway.routes.projects import tasks as pm_tasks

    from tests.unit._projects_fakes import page

    ns, ids = seeded
    eng = _app_engine(ns)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a}
            )
            yield conn

    monkeypatch.setattr(pm_tasks, "_tenant_session", _session)
    try:
        out = await pm_tasks.list_tasks(
            q="invoice approval", page=page(), user=member_user(ANA),
        )
    finally:
        await eng.dispose()
    rows = out["rows"] if isinstance(out, dict) else out.rows
    assert [str(r["id"] if isinstance(r, dict) else r.id) for r in rows] == [
        ids["described"]
    ]
