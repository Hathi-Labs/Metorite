"""WS-57 TR-5 — links from a task's text: ``#N`` and GitHub or document URLs.

Spec: ``project-docs/specs/task_references.md`` §6 TR-5. The module is
``gateway/routes/projects/text_links.py``.

Three halves.

**The shared grammar.** ``tests/fixtures/task_ref_cases.json`` holds two
kinds of case. ``cases`` are plain text, and this file runs the Python finder
over them. ``markdown_cases`` are a description or a comment, and this file
reads them through ``prose_of`` and the finder.
``workbench/control_plane/src/lib/taskRefs.test.ts`` runs ``findTaskRefs``
over the first kind, and the card's own Markdown pipeline over the second. A
rule changed on one side fails one of the two suites.

**Hermetic.** The URL rules, the caps, the "new in this edit" rule, the pick,
the one insert of each table, the write paths that call the module, and
"never fails the save".

**R8** (a real Postgres, through the real ``/projects`` router, as the
non-privileged role of the phase-4 rehearsal). A hermetic fake agrees with
any SQL, so these need the database:

* (a) ``#12`` names #12 of the task's own root, and NOTHING when that root
  cannot resolve it: no fallback to another root (review round 1). A #12
  that moved roots, a #12 that was deleted and a #12 the caller cannot see
  each link nothing and draw no pill;
* (b) a task the caller cannot see is never linked, and the refs read does
  not show it;
* (c) a GitHub URL in a comment becomes a reference with ``via = 'text'``,
  with no ``fetched_at`` and no call to GitHub;
* (d) saving the same text again adds nothing, a link in the other
  direction counts, an edit that drops ``#12`` keeps the link, and a link a
  member removed stays removed until somebody types ``#12`` again.

The R8 class carries ``_DB_GATE``, so it skips where no ladder URL is set.
"""

from __future__ import annotations

import ast
import contextlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import refs, text_links
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_task_refs_route import _call, _principal

_REPO = Path(__file__).resolve().parents[2]
_CASES = _REPO / "tests" / "fixtures" / "task_ref_cases.json"
_PROJECTS = _REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"


# ── The shared grammar ──────────────────────────────────────────────────────


def _shared() -> dict[str, Any]:
    return json.loads(_CASES.read_text(encoding="utf-8"))


def test_the_case_file_is_present_and_not_trivial() -> None:
    """A case file that quietly emptied would leave both suites green."""
    shared = _shared()
    for kind in ("cases", "markdown_cases"):
        cases = shared[kind]
        assert len(cases) >= 30, kind
        assert any(c["numbers"] for c in cases) and any(not c["numbers"] for c in cases), kind


@pytest.mark.parametrize("case", _shared()["cases"], ids=lambda c: c["name"])
def test_the_python_finder_agrees_with_the_case_file(case: dict[str, Any]) -> None:
    found = text_links.find_task_refs(case["text"])
    assert [m.number for m in found] == case["numbers"]
    for m in found:
        assert case["text"][m.start:m.end] == f"#{m.number}"


@pytest.mark.parametrize("case", _shared()["markdown_cases"], ids=lambda c: c["name"])
def test_the_markdown_layer_agrees_with_the_card(case: dict[str, Any]) -> None:
    """The card's pills, as `taskRefs.test.ts` counts them through remark,
    are the numbers the gateway reads."""
    words = text_links.prose_of(case["text"])
    assert [m.number for m in text_links.find_task_refs(words)] == case["numbers"]


# ── Hermetic: the URLs ──────────────────────────────────────────────────────


def _new(value: str, markdown: bool = True, old: str | None = None) -> text_links.Scan:
    return text_links.scan([(value, markdown, old)])


def test_a_title_is_plain_text() -> None:
    """A title is not Markdown: backticks in it are characters."""
    assert _new("Fix `#12` now", markdown=False).numbers == [12]


def test_the_urls_lose_a_sentences_marks_and_a_links_bracket() -> None:
    assert text_links.find_urls(
        "See https://github.com/a/b/pull/1. And (https://github.com/a/b/issues/2), "
        "[x](https://github.com/a/b/commit/abcdef1) <https://docs.google.com/document/d/1AbCdEfGhIjK/edit>"
    ) == [
        "https://github.com/a/b/pull/1",
        "https://github.com/a/b/issues/2",
        "https://github.com/a/b/commit/abcdef1",
        "https://docs.google.com/document/d/1AbCdEfGhIjK/edit",
    ]


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://github.com/acme/widgets/pull/12", True),
        ("https://github.com/acme/widgets/issues/3", True),
        ("https://github.com/acme/widgets/commit/abcdef1", True),
        ("https://github.com/acme/widgets/tree/main", True),
        ("https://github.com/acme/widgets/pull/12#issuecomment-99", True),
        ("https://docs.google.com/document/d/1AbCdEfGhIjK/edit", True),
        ("https://www.figma.com/file/AbCdEfGhIjKl/x", True),
        ("https://github.com/acme/widgets", False),
        ("https://github.com/acme", False),
        ("https://gitlab.com/g/p/-/merge_requests/4", False),
        ("https://example.com/page", False),
    ],
)
def test_only_a_github_shape_or_a_document_becomes_a_reference(url: str, kept: bool) -> None:
    assert bool(_new(f"see {url} now").refs) is kept, url


def test_a_url_in_code_adds_no_reference() -> None:
    body = "`https://github.com/a/b/pull/1`\n```\nhttps://github.com/a/b/pull/2\n```\n\n    https://github.com/a/b/pull/3"
    assert _new(body).refs == []


def test_a_refused_url_is_skipped_and_the_rest_still_count() -> None:
    found = _new("https://github.com/../../pull/1 and https://github.com/a/b/pull/3")
    assert [r.ref for r in found.refs] == ["a/b#3"]


def test_one_number_and_one_reference_count_once_across_the_texts() -> None:
    found = text_links.scan([
        ("#12 and https://github.com/a/b/pull/3", False, None),
        ("again #12 and https://github.com/A/B/pull/3/files", True, None),
    ])
    assert found.numbers == [12]
    assert [r.ref for r in found.refs] == ["a/b#3"]


def test_each_cap_holds_and_counts_what_it_left() -> None:
    many = " ".join(f"#{n}" for n in range(1, 26))
    urls = " ".join(f"https://github.com/a/b/pull/{n}" for n in range(1, 26))
    found = _new(many + " " + urls)
    assert found.numbers == list(range(1, 21)) and found.numbers_left == 5
    assert len(found.refs) == 20 and found.refs_left == 5
    assert text_links.MAX_TASK_REFS == 20 and text_links.MAX_URL_REFS == 20


# ── Hermetic: only what is new in this edit (review round 1) ────────────────


def test_a_number_or_url_the_old_value_held_is_not_new() -> None:
    old = "Blocked on #12, see https://github.com/a/b/pull/3"
    assert _new(old, old=old).numbers == [] and _new(old, old=old).refs == []
    grown = _new(old + " and #13 and https://github.com/a/b/pull/4", old=old)
    assert grown.numbers == [13]
    assert [r.ref for r in grown.refs] == ["a/b#4"]


def test_a_number_that_left_and_came_back_is_new() -> None:
    assert _new("Blocked on #12", old="Blocked").numbers == [12]


def test_the_old_value_is_read_by_the_same_markdown_rules() -> None:
    """`#12` in old code and in new words is new: the card drew no pill
    before, and draws one now."""
    assert _new("#12 now", old="`#12` then").numbers == [12]


def test_saved_texts_pairs_each_field_with_its_old_value() -> None:
    before = SimpleNamespace(title="Old #1", description="Old #2")
    assert text_links.saved_texts({"title": "a", "importance": 3}, before) == [("a", False, "Old #1")]
    assert text_links.saved_texts({"description": "b"}, before) == [("b", True, "Old #2")]
    assert text_links.saved_texts({"description": "b"}) == [("b", True, None)]
    assert text_links.saved_texts({"due_at": None}, before) == []
    assert text_links.saved_texts(None) == []


# ── Hermetic: the pick ──────────────────────────────────────────────────────


def _hit(tid: str, archived: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=tid, archived=archived)


def test_the_pick_takes_one_task_or_the_one_open_task() -> None:
    assert text_links.pick_task([_hit("a")]).id == "a"
    assert text_links.pick_task([_hit("a"), _hit("b", archived=True)]).id == "a"
    assert text_links.pick_task([_hit("a"), _hit("b")]) is None
    assert text_links.pick_task([_hit("a", True), _hit("b", True)]) is None
    assert text_links.pick_task([]) is None


# ── Hermetic: the seams ─────────────────────────────────────────────────────


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in _PROJECTS.glob("*.py")}


def test_each_table_has_one_insert_and_this_module_writes_neither_itself() -> None:
    """The text path reuses the two inserts. A copy of the SQL would drift."""
    sources = _sources()
    for table, home in (("pm_task_links", "relations.py"), ("pm_task_refs", "refs.py")):
        writers = sorted(n for n, s in sources.items() if f"INSERT INTO {table}" in s)
        assert writers == [home], (table, writers)
    module = sources["text_links.py"]
    assert "insert_task_link(" in module and "insert_ref(" in module
    assert "httpx" not in module and "api.github.com" not in module


#: Every route that saves a title, a description or a comment body and calls
#: the module. A new write path of that text must join this list, or say in
#: the spec why it does not.
HOOKED: dict[str, set[str]] = {
    "tasks.py": {"create_task", "patch_task"},
    "activities.py": {"add_comment", "edit_comment"},
    "bulk.py": {"bulk_edit"},
    "personal.py": {"capture", "capture_batch", "add_my_steps", "organize_my_task"},
}


def test_the_write_paths_call_the_module() -> None:
    sources = _sources()
    for name, routes in HOOKED.items():
        tree = ast.parse(sources[name])
        calling = {
            fn.name for fn in ast.walk(tree)
            if isinstance(fn, ast.AsyncFunctionDef)
            and any(
                isinstance(c, ast.Call) and getattr(c.func, "id", None) == "link_from_text"
                for c in ast.walk(fn)
            )
        }
        assert routes <= calling, (name, routes - calling)


def _no_session(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("a session was opened")


async def test_text_with_nothing_new_opens_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_links, "_tenant_session", _no_session)
    user = SimpleNamespace(email="a@x.test")
    await text_links.link_from_text(user, [
        ("t", [("plain words", False, None), (None, True, None), ("see #12", True, "see #12")]),
    ])


async def test_a_save_of_more_than_the_budget_links_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bulk edit of 26 tasks with `#12` in the patch reads nothing and
    opens no transaction, so the client does not wait on it (P2-4)."""
    monkeypatch.setattr(text_links, "_tenant_session", _no_session)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(text_links, "_log", SimpleNamespace(
        info=lambda event, **kw: logged.append({"event": event, **kw}),
        warning=lambda event, **kw: logged.append({"event": event, **kw}),
    ))
    user = SimpleNamespace(email="a@x.test")
    many = [(f"t{n}", [("see #12", True, None)]) for n in range(text_links.MAX_TASKS_PER_SAVE + 1)]
    await text_links.link_from_text(user, many)
    assert logged == [{"event": "projects.text_links.capped", "tasks": 26, "tasks_left": 26}]
    assert text_links.MAX_TASKS_PER_SAVE == 25


async def test_a_database_failure_never_fails_the_save(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.asynccontextmanager
    async def _broken(*_a: Any, **_k: Any):
        raise OperationalError("SELECT 1", {}, Exception("database down"))
        yield  # pragma: no cover

    monkeypatch.setattr(text_links, "_tenant_session", _broken)
    user = SimpleNamespace(email="a@x.test")
    await text_links.link_from_text(user, [("t", [("see #12", False, None)])])


async def test_a_defect_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a database failure is swallowed. A programming error raises, so
    it shows as a defect and not as a class name in a log line."""
    @contextlib.asynccontextmanager
    async def _buggy(*_a: Any, **_k: Any):
        raise TypeError("a defect")
        yield  # pragma: no cover

    monkeypatch.setattr(text_links, "_tenant_session", _buggy)
    user = SimpleNamespace(email="a@x.test")
    with pytest.raises(TypeError):
        await text_links.link_from_text(user, [("t", [("see #12", False, None)])])


# ── R8: the links on a real database ────────────────────────────────────────

A_EMAIL = "tr5-a@text-links-r8.test"
M_EMAIL = "tr5-m@text-links-r8.test"
PR_URL = "https://github.com/acme/widgets/pull/7"


def _seed_root(
    p: Any, org: str, email: str, *, grant: str | None = None, parent: str | None = None,
) -> tuple[str, str]:
    """A project with one lane, as the RLS-bypassing admin. With ``parent``,
    a child project that uses its parent's lane."""
    project, status = str(uuid.uuid4()), str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
            "parent_project_id) VALUES (:id, :o, 'tr5', :e, :own, :parent)"),
            {"id": project, "o": org, "e": email, "own": parent is None, "parent": parent})
        if grant is not None:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": project, "s": grant, "e": email})
        if parent is None:
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
                {"id": status, "o": org, "p": project})
    return project, status


def _seed_task(
    p: Any, org: str, root: tuple[str, str], number: int, title: str,
    *, project: str | None = None,
) -> str:
    task = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :r, :n, :s, :t, :e)"),
            {"id": task, "o": org, "p": project or root[0], "r": root[0], "n": number,
             "s": root[1], "t": title, "e": A_EMAIL})
    return task


def _scalar(p: Any, sql: str, **params: Any) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _links_of(p: Any, task: str) -> set[tuple[str, str, str]]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT source_task_id::text, target_task_id::text, link_type FROM pm_task_links "
            " WHERE source_task_id = CAST(:t AS uuid) OR target_task_id = CAST(:t AS uuid)"),
            {"t": task}).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def _link_activities(p: Any, task: str) -> int:
    return int(_scalar(
        p, "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
        "AND type = 'link'", t=task))


async def _pills(w: Any, user: Any, root: str, numbers: str) -> set[str]:
    """The tasks the card's pills would name: the refs read with the root."""
    read = await _call(w.p, user, "GET", f"/projects/search/refs?numbers={numbers}&root={root}")
    assert read.status_code == 200, read.text
    return {r["id"] for r in read.json()["rows"]}


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Org A, with three roots and one hidden root.

    * root 1 holds the source task #1 and #12;
    * root 2 holds another #12 and the only #13, and one #14;
    * root 3 holds another #14;
    * the hidden root holds #40, and the restricted member M has no grant on
      it. M's own root holds M's task #5.
    """
    p, org = promoted, promoted.org_a
    r1, r2, r3 = (_seed_root(p, org, A_EMAIL) for _ in range(3))
    hidden = _seed_root(p, org, A_EMAIL, grant=A_EMAIL)
    mine = _seed_root(p, org, M_EMAIL, grant=M_EMAIL)
    return SimpleNamespace(
        p=p,
        a=_principal(A_EMAIL, org),
        m=_principal(M_EMAIL, org, unrestricted=False),
        r1=r1[0],
        r2=r2,
        source=_seed_task(p, org, r1, 1, "The source"),
        own12=_seed_task(p, org, r1, 12, "Twelve in my root"),
        other12=_seed_task(p, org, r2, 12, "Twelve elsewhere"),
        only13=_seed_task(p, org, r2, 13, "Thirteen"),
        two14=(_seed_task(p, org, r2, 14, "Fourteen A"), _seed_task(p, org, r3, 14, "Fourteen B")),
        hidden40=_seed_task(p, org, hidden, 40, "Hidden forty"),
        m_root=mine[0],
        m_rt=mine,
        m_task=_seed_task(p, org, mine, 5, "M's own task"),
    )


@pytest.fixture
def w(world, monkeypatch):
    """The world with no link, no reference and no link activity left, and a
    GitHub token reader that fails the test if anything calls it."""
    async def _no_github() -> None:
        raise AssertionError("the save path read GitHub")

    monkeypatch.setattr(refs, "_github_token", _no_github)
    tasks = [world.source, world.own12, world.other12, world.only13, *world.two14,
             world.hidden40, world.m_task]
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM pm_task_links WHERE source_task_id = ANY(CAST(:t AS uuid[])) "
            "OR target_task_id = ANY(CAST(:t AS uuid[]))"), {"t": tasks})
        c.execute(text("DELETE FROM pm_task_refs WHERE task_id = ANY(CAST(:t AS uuid[]))"),
                  {"t": tasks})
        c.execute(text(
            "DELETE FROM pm_activities WHERE type = 'link' "
            "AND task_id = ANY(CAST(:t AS uuid[]))"), {"t": tasks})
        c.execute(text(
            "UPDATE pm_tasks SET title = 'The source', description = NULL "
            " WHERE id = CAST(:t AS uuid)"), {"t": world.source})
    return world


@_DB_GATE
class TestTextLinksOnARealDatabase:
    async def test_only_the_own_root_resolves_a_number(self, w):
        """(a) #12 names #12 of this root. #13 lives only in root 2, and #14
        in two other roots: with no fallback, neither links. #1 is the task
        itself. #9999 names nothing."""
        saved = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}",
                            {"description": "See #12, #13, #14, #1 and #9999."})
        assert saved.status_code == 200, saved.text
        assert _links_of(w.p, w.source) == {(w.source, w.own12, "relates_to")}
        assert _link_activities(w.p, w.source) == 1
        with w.p.admin_engine.connect() as c:
            meta = c.execute(text(
                "SELECT meta FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                "AND type = 'link'"), {"t": w.source}).scalar()
        assert (meta["target"], meta["type"]) == (w.own12, "relates_to")

    async def test_the_refs_read_with_a_root_answers_that_root_only(self, w):
        """The card's pills read the same rule through the route. The chat's
        read, with no root, keeps its organization-wide answer."""
        plain = await _call(w.p, w.a, "GET", "/projects/search/refs?numbers=12,13")
        assert plain.status_code == 200
        assert {r["id"] for r in plain.json()["rows"]} == {w.own12, w.other12, w.only13}
        assert await _pills(w, w.a, w.r1, "12,13") == {w.own12}
        junk = await _call(w.p, w.a, "GET", "/projects/search/refs?numbers=12&root=nope")
        assert junk.status_code == 200 and junk.json()["rows"] == []

    async def test_a_number_that_moved_roots_links_nothing_and_draws_no_pill(self, w):
        """(a) #12 moved to another root, which renumbers it. Root X now has
        no #12, and the #12 of root Y is not it."""
        org = w.p.org_a
        rx, ry = _seed_root(w.p, org, A_EMAIL), _seed_root(w.p, org, A_EMAIL)
        source = _seed_task(w.p, org, rx, 1, "Source X")
        x12 = _seed_task(w.p, org, rx, 12, "Twelve, to move")
        y12 = _seed_task(w.p, org, ry, 12, "Twelve of Y")
        moved = await _call(w.p, w.a, "POST", f"/projects/tasks/{x12}/move", {"project_id": ry[0]})
        assert moved.status_code == 200, moved.text
        assert moved.json()["task_number"] != 12
        saved = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{source}",
                            {"description": "After #12."})
        assert saved.status_code == 200
        assert _links_of(w.p, source) == set()
        assert _links_of(w.p, y12) == set()
        assert await _pills(w, w.a, rx[0], "12") == set()

    async def test_a_deleted_number_links_nothing_and_draws_no_pill(self, w):
        """(a) #12 was deleted. The counter never gives #12 out again, so the
        hole stays, and a fallback would fill it for good."""
        org = w.p.org_a
        rx, ry = _seed_root(w.p, org, A_EMAIL), _seed_root(w.p, org, A_EMAIL)
        source = _seed_task(w.p, org, rx, 1, "Source X")
        x12 = _seed_task(w.p, org, rx, 12, "Twelve, to delete")
        y12 = _seed_task(w.p, org, ry, 12, "Twelve of Y")
        gone = await _call(w.p, w.a, "DELETE", f"/projects/tasks/{x12}")
        assert gone.status_code == 200, gone.text
        saved = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{source}",
                            {"description": "After #12."})
        assert saved.status_code == 200
        assert _links_of(w.p, source) == set() and _links_of(w.p, y12) == set()
        assert await _pills(w, w.a, rx[0], "12") == set()

    async def test_a_number_hidden_from_the_caller_links_nothing(self, w):
        """(a) and (b). M sees one project of root P through a grant, and not
        the #12 that sits in P itself. M's own other root holds a #12 M can
        see. M's text links neither, and M's pill names neither."""
        org = w.p.org_a
        rp = _seed_root(w.p, org, A_EMAIL)
        child = _seed_root(w.p, org, A_EMAIL, grant=M_EMAIL, parent=rp[0])
        m_source = _seed_task(w.p, org, rp, 1, "M's task in P", project=child[0])
        p12 = _seed_task(w.p, org, rp, 12, "Twelve, hidden from M")
        m12 = _seed_task(w.p, org, w.m_rt, 12, "Twelve M can see")
        saved = await _call(w.p, w.m, "PATCH", f"/projects/tasks/{m_source}",
                            {"description": "Blocked on #12."})
        assert saved.status_code == 200, saved.text
        assert _links_of(w.p, m_source) == set()
        assert _links_of(w.p, p12) == set() and _links_of(w.p, m12) == set()
        assert await _pills(w, w.m, rp[0], "12") == set()
        # A sees P's #12, and the same words link it.
        seen = await _call(w.p, w.a, "POST", f"/projects/tasks/{m_source}/comments",
                           {"body": "Blocked on #12."})
        assert seen.status_code == 201
        assert _links_of(w.p, m_source) == {(m_source, p12, "relates_to")}

    async def test_a_hidden_task_of_another_root_is_not_shown(self, w):
        """(b) M has no grant on the hidden root, so #40 names nothing for M."""
        saved = await _call(w.p, w.m, "POST", f"/projects/tasks/{w.m_task}/comments",
                            {"body": "Is this #40?"})
        assert saved.status_code == 201, saved.text
        assert _links_of(w.p, w.m_task) == set() and _links_of(w.p, w.hidden40) == set()
        read = await _call(w.p, w.m, "GET", "/projects/search/refs?numbers=40")
        assert read.status_code == 200 and read.json()["rows"] == []

    async def test_a_github_url_in_a_comment_becomes_a_text_reference(self, w):
        """(c) The reference is stored with `via = 'text'` and no read of
        GitHub, so `fetched_at` is NULL and the next list reads it."""
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.source}/comments",
                           {"body": f"The fix is {PR_URL}.", "parent_id": None},
                           headers={"X-Actor-Via": "chat:projects-assistant"})
        assert made.status_code == 201, made.text
        with w.p.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT kind, provider, ref, via, fetched_at, created_by FROM pm_task_refs "
                " WHERE task_id = CAST(:t AS uuid)"), {"t": w.source}).one()
            body, via = c.execute(text(
                "SELECT body, meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                "AND type = 'link'"), {"t": w.source}).one()
        assert tuple(row) == ("pull_request", "github", "acme/widgets#7", "text", None, A_EMAIL)
        assert body == "Added a reference: widgets#7"
        assert via == "chat:projects-assistant"

    async def test_saving_the_same_text_again_adds_nothing(self, w):
        """(d) A second save of the same words writes no link, no reference
        and no activity. A link in the other direction counts as a link, and
        an edit that drops #12 keeps the link."""
        words = f"Blocked on #12, see {PR_URL}"
        for _ in range(2):
            saved = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}",
                                {"description": words})
            assert saved.status_code == 200, saved.text
        assert _links_of(w.p, w.source) == {(w.source, w.own12, "relates_to")}
        assert int(_scalar(w.p, "SELECT count(*) FROM pm_task_refs WHERE task_id = "
                                "CAST(:t AS uuid)", t=w.source)) == 1
        assert _link_activities(w.p, w.source) == 2

        # A link from the other end, made by hand, then a title that names it.
        back = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.other12}/links",
                           {"target_task_id": w.only13, "link_type": "blocks"})
        assert back.status_code == 201
        titled = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.only13}",
                             {"title": "Thirteen, after #12"})
        assert titled.status_code == 200
        # #12 in root 2 is `other12`, which already links to #13.
        assert _links_of(w.p, w.only13) == {(w.other12, w.only13, "blocks")}

        dropped = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}",
                              {"description": "No numbers now."})
        assert dropped.status_code == 200
        assert _links_of(w.p, w.source) == {(w.source, w.own12, "relates_to")}

    async def test_a_removed_link_stays_removed_until_the_number_is_typed_again(self, w):
        """(d, P2-2) A member removes the link. A save of the same words, as
        the inbox card sends on each save, does not bring it back. Typing #12
        into words that did not hold it does."""
        words = "Blocked on #12"
        made = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}", {"description": words})
        assert made.status_code == 200
        link_id = str(_scalar(
            w.p, "SELECT id FROM pm_task_links WHERE source_task_id = CAST(:t AS uuid)",
            t=w.source))
        removed = await _call(w.p, w.a, "DELETE", f"/projects/tasks/{w.source}/links/{link_id}")
        assert removed.status_code == 200
        again = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}",
                            {"title": "The source", "description": words})
        assert again.status_code == 200
        assert _links_of(w.p, w.source) == set()
        # A comment edit compares with the comment's old body too.
        posted = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.source}/comments",
                             {"body": "See #12"})
        assert posted.status_code == 201
        assert _links_of(w.p, w.source) == {(w.source, w.own12, "relates_to")}
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_task_links WHERE source_task_id = CAST(:t AS uuid)"),
                      {"t": w.source})
        edited = await _call(w.p, w.a, "PATCH", f"/projects/comments/{posted.json()['id']}",
                             {"body": "See #12, really"})
        assert edited.status_code == 200
        assert _links_of(w.p, w.source) == set()
        # Typed again: the words lose #12, then gain it.
        for text_now in ("Blocked", "Blocked on #12"):
            step = await _call(w.p, w.a, "PATCH", f"/projects/tasks/{w.source}",
                               {"description": text_now})
            assert step.status_code == 200
        assert _links_of(w.p, w.source) == {(w.source, w.own12, "relates_to")}

    async def test_a_new_task_and_a_bulk_title_link_from_their_text(self, w):
        """The create and the bulk paths call the same module."""
        org = w.p.org_a
        fresh = _seed_root(w.p, org, A_EMAIL)
        fresh13 = _seed_task(w.p, org, fresh, 13, "Thirteen of the fresh root")
        made = await _call(w.p, w.a, "POST", "/projects/tasks", {
            "project_id": fresh[0], "title": "New, about #13",
            "description": "and https://docs.google.com/document/d/1AbCdEfGhIjK/edit",
        })
        assert made.status_code == 201, made.text
        new_id = made.json()["id"]
        assert _links_of(w.p, new_id) == {(new_id, fresh13, "relates_to")}
        assert int(_scalar(w.p, "SELECT count(*) FROM pm_task_refs WHERE task_id = "
                                "CAST(:t AS uuid) AND via = 'text'", t=new_id)) == 1
        bulk = await _call(w.p, w.a, "POST", "/projects/tasks/bulk", {
            "task_ids": [w.source], "patch": {"title": "Source, see #12"},
        })
        assert bulk.status_code == 200, bulk.text
        assert (w.source, w.own12, "relates_to") in _links_of(w.p, w.source)
