"""The topics of the chat list, with no database (WS-CO CO-2a, task 3501).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163.1,
D163.2). Module: ``gateway/chat_topic.py``. Routes:
``gateway/routes/chat_topics.py``. The R8 half is ``test_chat_topic_r8.py``.

Fences (R7):

* ``chat-topic-one-module``: only the store module and the member purge name
  the table ``chat_topic`` in Python, and no file of the control plane names
  it. A new reader or writer fails here until its slice adds it. Mutation:
  name the table in ``routes/chat_topics.py``.
* ``chat-topic-member-rule``: each read holds the rule of D163.2 in its
  WHERE (company, or the caller's own member topic), and each write holds
  the write rule. Mutation: drop ``_VISIBLE`` from ``_LIST_SQL``, or the
  admin bind from ``_HIDE_SQL``.
* ``chat-topic-name``: a name is trimmed, 1 to 40 characters, with no control
  character, and the route answers 422 before any SQL. Mutation: drop the
  category check of ``clean_name``.
* ``chat-topic-seed``: the seed names are the teams of ``centers.ts`` plus
  General. Mutation: add a Center to ``centers.ts`` and not to ``TEAMS``.
* ``chat-topic-dark``: ``CHAT_TOPICS_ORGS`` is the switch. Off, each route
  answers 404 before any SQL. On, the same request reaches the store, so the
  404 is the switch and not a missing route. The real gateway app serves the
  four routes. Mutation: drop ``require_topics`` from the router.
* ``chat-topic-session-identity``: the member is the session's. A body that
  names an owner, a scope or an org gets 422. A query parameter changes
  nothing. Mutation: read the member from a query parameter.
* ``chat-topic-expand-only``: the migration creates one table with FORCE RLS,
  sets ``lock_timeout``, and changes no other object.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
STORE = GATEWAY / "chat_topic.py"
PURGE = GATEWAY / "routes" / "admin" / "members.py"
TABLE = "chat_topic"
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_topic ("

#: The Python files that may name the table: the store, and the purge row.
ALLOWED_PY = {STORE, PURGE}
#: A SQL reference to the table, or the table as a quoted literal. The word
#: edge keeps out ``chat_topic_note`` (CO-16) and ``chat_topics``.
_NAMES_TABLE = re.compile(
    r"(?i)(?:\b(?:from|join|update|into|table)\s+(?:public\.)?chat_topic\b(?!_))"
    r"|(?:[\"']chat_topic[\"'])")
_ANY_NAME = re.compile(r"\bchat_topic\b(?!_)")


def _migration() -> Path:
    hits = [p for p in sorted((ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _py_files() -> list[Path]:
    out: list[Path] = []
    for root in (ROOT / "apps", ROOT / "packages", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if not parts & {".venv", "node_modules", "__pycache__", "tests"}:
                out.append(path)
    return out


# ── chat-topic-one-module ───────────────────────────────────────────────────


def _python_namers(files: list[Path]) -> set[Path]:
    return {p for p in files if _NAMES_TABLE.search(p.read_text(encoding="utf-8",
                                                                errors="replace"))}


def test_only_the_store_and_the_purge_name_the_table_in_python() -> None:
    namers = _python_namers(_py_files())
    assert namers == ALLOWED_PY, sorted(str(p.relative_to(ROOT)) for p in namers ^ ALLOWED_PY)


def test_no_file_of_the_control_plane_names_the_table() -> None:
    src = ROOT / "workbench" / "control_plane" / "src"
    named = [p for p in src.rglob("*") if p.suffix in {".ts", ".tsx"}
             and "node_modules" not in p.parts
             and _ANY_NAME.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert named == []


def test_the_name_fence_can_fail(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text('SQL = "SELECT name FROM chat_topic t"\n', encoding="utf-8")
    assert _python_namers([planted]) == {planted}
    quiet = tmp_path / "quiet.py"
    quiet.write_text('SQL = "SELECT body FROM chat_topic_note"\n# routes/chat_topics.py\n',
                     encoding="utf-8")
    assert _python_namers([quiet]) == set()


# ── chat-topic-member-rule ──────────────────────────────────────────────────


def test_each_read_holds_the_member_rule() -> None:
    from gateway import chat_topic as store

    assert store._VISIBLE == (
        "(t.scope = 'company' OR (t.scope = 'member' AND t.owner_email = :member))")
    for sql in (store._LIST_SQL, store._ONE_SQL, store._LOCK_SQL):
        assert store._VISIBLE in sql, sql
    assert "t.hidden_at IS NULL OR CAST(:with_hidden AS boolean)" in store._LIST_SQL
    # A hidden company topic is an admin's to read, by id too (reviewer note).
    assert store._SHOWN == "(t.hidden_at IS NULL OR CAST(:admin AS boolean))"
    for sql in (store._ONE_SQL, store._LOCK_SQL):
        assert store._SHOWN in sql, sql


def test_the_merge_locks_both_rows_in_id_order_before_it_checks() -> None:
    """Verifier P1 and reviewer P2: two merges that cross must not both win.
    ``_LOCK_SQL`` takes both rows ``FOR UPDATE`` in id order, and the merge
    reads it before any check and before the delete. Mutation: drop
    ``FOR UPDATE``, or read the rows with ``get_topic`` -> red here, and
    ``test_merge_*`` of the R8 suite fails on the real database."""
    import inspect

    from gateway import chat_topic as store

    assert store._LOCK_SQL.rstrip().endswith("ORDER BY t.id FOR UPDATE")
    assert "t.id IN (CAST(:id AS uuid), CAST(:into AS uuid))" in store._LOCK_SQL
    src = inspect.getsource(store.merge_company_topics)
    assert "get_topic(" not in src
    assert src.index("_LOCK_SQL") < src.index("source is None")
    assert src.index("_LOCK_SQL") < src.index("_MERGE_SQL")


def test_the_admin_capability_is_the_house_rule() -> None:
    """Reviewer note: ``TOPIC_ADMIN`` is the house rule for an org-wide
    write, so the two cannot drift."""
    from gateway.chat_topic import TOPIC_ADMIN
    from gateway.routes.projects.core import ORG_VOCABULARY_WRITE

    assert TOPIC_ADMIN == ORG_VOCABULARY_WRITE


def test_each_write_holds_the_write_rule() -> None:
    from gateway import chat_topic as store

    assert store._WRITABLE in store._RENAME_SQL
    assert "t.owner_email = :member" in store._WRITABLE
    assert "t.scope = 'company' AND CAST(:admin AS boolean)" in store._WRITABLE
    for sql in (store._HIDE_SQL, store._MERGE_SQL):
        assert "t.scope = 'company' AND CAST(:admin AS boolean)" in sql, sql
    # A merge target is a company topic that shows.
    assert "g.scope = 'company' AND g.hidden_at IS NULL" in store._MERGE_SQL
    # A create keys the owner on the session member, and never names an org.
    assert "VALUES (:name, 'member', :member)" in store._CREATE_SQL
    assert "organization_id" not in store._CREATE_SQL
    assert "ON CONFLICT DO NOTHING" in store._CREATE_SQL
    # No statement names the org: RLS and the DEFAULT bind it.
    for sql in (store._LIST_SQL, store._ONE_SQL, store._RENAME_SQL, store._HIDE_SQL,
                store._MERGE_SQL):
        assert "organization_id" not in sql, sql


def test_the_find_of_the_automatic_pass_holds_the_member_rule() -> None:
    """CO-1 names a topic through ``find_or_create_member_topic``. Its read
    holds the rule of D163.2, skips a hidden company topic, and matches the
    name in any case. Its create is the member create, never a company row.
    Mutation: drop ``_VISIBLE`` from ``_FIND_SQL`` -> red here and in (h) of
    the R8 suite."""
    import inspect

    from gateway import chat_topic as store

    assert store._VISIBLE in store._FIND_SQL
    assert "(t.scope = 'member' OR t.hidden_at IS NULL)" in store._FIND_SQL
    assert "lower(t.name) = lower(:name)" in store._FIND_SQL
    assert "organization_id" not in store._FIND_SQL
    src = inspect.getsource(store.find_or_create_member_topic)
    assert "create_member_topic(" in src and "clean_name(" in src
    assert "'company'" not in src


@pytest.mark.parametrize("name", ["", "x" * 41, "a\nb"])
def test_the_automatic_pass_cannot_name_a_bad_topic(name: str) -> None:
    from gateway.chat_topic import TopicNameError, find_or_create_member_topic

    class _NoSql:
        def execute(self, *a: Any, **k: Any):
            raise AssertionError("SQL ran for a bad name")

    with pytest.raises(TopicNameError):
        find_or_create_member_topic(_NoSql(), "ana@co2.test", name)


def test_the_wire_shape_names_no_owner() -> None:
    from gateway.chat_topic import Topic

    t = Topic(id="t-1", name="Sales", scope="member", hidden_at=None)
    assert t.as_json() == {"id": "t-1", "name": "Sales", "scope": "member",
                           "mine": True, "hidden": False}


def test_the_member_key_is_the_address_in_lower_case() -> None:
    from gateway.chat_topic import member_key

    assert member_key("  Dana.Mixed@CO2.test ") == "dana.mixed@co2.test"
    assert member_key(None) == ""


@pytest.mark.parametrize("raw", [
    "11111111-2222-3333-4444-555555555555", "11111111222233334444555555555555"])
def test_an_id_takes_its_canonical_shape(raw: str) -> None:
    from gateway.chat_topic import topic_id

    assert topic_id(raw) == "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("raw", ["", "abc", "1; DROP TABLE x", None, 7, "../etc"])
def test_an_id_of_another_shape_is_none(raw: Any) -> None:
    from gateway.chat_topic import topic_id

    assert topic_id(raw) is None


# ── chat-topic-name ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "name"), [
    ("Sales", "Sales"),
    ("  Board prep  ", "Board prep"),
    ("x" * 40, "x" * 40),
    ("Prix \u00e9t\u00e9", "Prix \u00e9t\u00e9"),
    ("Team \U0001F468\u200d\U0001F469", "Team \U0001F468\u200d\U0001F469"),
])
def test_a_good_name_is_trimmed(raw: str, name: str) -> None:
    from gateway.chat_topic import clean_name

    assert clean_name(raw) == name


@pytest.mark.parametrize("raw", [
    "", "   ", "x" * 41, "Sa\nles", "Sa\x00les", "Sa\tles", "Sa\u202eles", "Sa\u2028les",
    "Sa\x7fles", None, 12,
])
def test_a_bad_name_is_refused(raw: Any) -> None:
    from gateway.chat_topic import TopicNameError, clean_name

    with pytest.raises(TopicNameError):
        clean_name(raw)


def _cp(*points: int) -> str:
    """A string of code points. Each case names its points, so no editor or
    tool can change an invisible character in this file."""
    return "".join(chr(p) for p in points)


#: The flag of England: a black flag, five tag characters (Cf) and a cancel tag.
ENGLAND = _cp(0x1F3F4, 0xE0067, 0xE0062, 0xE0065, 0xE006E, 0xE0067, 0xE007F)
#: U+1FAE9, an emoji of Unicode 16. An older table reads it as unassigned (Cn).
NEW_EMOJI = _cp(0x1FAE9)
#: A family: three emoji joined by two ZWJ (Cf).
FAMILY = _cp(0x1F468, 0x200D, 0x1F469, 0x200D, 0x1F467)
NBSP = _cp(0xA0)


@pytest.mark.parametrize(("raw", "name"), [
    ("Tired " + NEW_EMOJI, "Tired " + NEW_EMOJI),
    ("Team " + ENGLAND, "Team " + ENGLAND),
    ("Family " + FAMILY, "Family " + FAMILY),
    (NBSP + "Sales" + NBSP + " ", "Sales"),
    ("Zero" + _cp(0x200B) + "width", "Zero" + _cp(0x200B) + "width"),
])
def test_an_emoji_a_flag_and_a_new_code_point_pass(raw: str, name: str) -> None:
    """Reviewer P2: a format character (Cf) and an unassigned code point (Cn)
    stay legal, so a flag, a joined emoji and a new emoji pass. A no-break
    space at either end goes in the trim. Mutation: refuse ``Cf`` or ``Cn``
    again -> red."""
    from gateway.chat_topic import clean_name

    assert clean_name(raw) == name


@pytest.mark.parametrize("point", [
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # the bidi embeddings and overrides
    0x2066, 0x2067, 0x2068, 0x2069,  # the bidi isolates
    0x2028, 0x2029,  # the line and paragraph marks (Zl, Zp)
    0x85, 0x9B, 0x1B,  # control characters (Cc)
    0xE000,  # private use (Co)
    0xD800,  # a surrogate (Cs)
])
def test_a_bidi_control_a_line_mark_and_a_control_are_refused(point: int) -> None:
    from gateway.chat_topic import TopicNameError, clean_name

    with pytest.raises(TopicNameError):
        clean_name("Sa" + _cp(point) + "les")


@pytest.mark.parametrize("points", [
    (0x200B,), (0x200C,), (0x200D,), (0x2060,), (0xFEFF,),  # zero-width (Cf)
    (0x3164,), (0x115F,), (0x1160,), (0xFFA0,),  # the Hangul fillers (Lo)
    (0xE0067, 0xE0062, 0xE007F),  # tag characters alone (Cf)
    (0xFE0F,), (0xFE00, 0xFE0F), (0xE0100,),  # variation selectors alone (Mn)
    (0x2800,), (0x034F,), (0x17B4, 0x17B5),  # a blank Braille cell and invisible marks
    (0x180B,), (0x180C,), (0x180D,), (0x180E,), (0x180F,),  # Mongolian selectors (verifier P3-7)
    (0x200B, 0x200D, 0xFEFF, 0x3164),  # several of them
    (0xAD,), (0x1D173,),  # a soft hyphen and a music format mark (Cf)
    (0xFDD0,), (0x10FFFF,),  # noncharacters: unassigned (Cn) in each Unicode table
])
def test_a_name_with_no_character_that_shows_is_refused(points: tuple[int, ...]) -> None:
    """CO-2a verifier P2: a name of invisible characters only showed as a
    blank row. A name must hold one character that shows. Mutation: drop the
    ``_shows`` check of ``clean_name`` -> red."""
    from gateway.chat_topic import TopicNameError, clean_name

    with pytest.raises(TopicNameError, match="that shows"):
        clean_name(_cp(*points))
    # With a space at each end too: the trim leaves the same characters.
    with pytest.raises(TopicNameError):
        clean_name(" " + _cp(*points) + " ")


def test_a_new_emoji_alone_follows_the_unicode_table_of_the_runtime() -> None:
    """A decision of CO-2b, not a wish: U+1FAE9 alone is refused while the
    runtime's table is older than Unicode 16 (it reads ``Cn``), and passes
    once the table knows it (``So``). With a word beside it, it passes in
    each table (``test_an_emoji_a_flag_and_a_new_code_point_pass``)."""
    import unicodedata

    from gateway.chat_topic import TopicNameError, clean_name

    if unicodedata.category(NEW_EMOJI) == "Cn":
        with pytest.raises(TopicNameError, match="that shows"):
            clean_name(NEW_EMOJI)
    else:
        assert clean_name(NEW_EMOJI) == NEW_EMOJI


@pytest.mark.parametrize("point", [0x200E, 0x200F, 0x061C])
def test_lrm_rlm_and_alm_are_refused_as_bidi_controls(point: int) -> None:
    """LRM, RLM and ALM can make a name read as another name, as the other
    bidi controls can. Mutation: drop them from ``_BIDI_CONTROLS`` -> red."""
    from gateway.chat_topic import TopicNameError, clean_name

    for raw in ("Sa" + _cp(point) + "les", _cp(point) + "Sales", "Sales" + _cp(point)):
        with pytest.raises(TopicNameError, match="control character"):
            clean_name(raw)


@pytest.mark.parametrize("raw", [
    ENGLAND, FAMILY, "Tired " + NEW_EMOJI, _cp(0x2764, 0xFE0F), "x" + _cp(0x200B),
    _cp(0x200B) + "7", "עברית",
    _cp(0x1820, 0x180B, 0x1821),  # a Mongolian word keeps its selector
])
def test_a_name_that_shows_passes(raw: str) -> None:
    """A flag, a joined family, a heart with its selector, a word beside a
    zero-width space, and Hebrew pass: each holds a character that shows."""
    from gateway.chat_topic import clean_name

    assert clean_name(raw) == raw


def _list_headings_of_the_control_plane() -> tuple[str, ...]:
    """``PINNED_GROUP`` and then ``DATE_GROUPS``, read from ``chatHistory.ts``."""
    src = (ROOT / "workbench" / "control_plane" / "src" / "lib" / "shell" / "chatHistory.ts"
           ).read_text(encoding="utf-8")
    pinned = re.search(r'export const PINNED_GROUP = "([^"]+)" as const;', src)
    dates = re.search(r"export const DATE_GROUPS = \[([^\]]+)\] as const;", src)
    assert pinned and dates, "the parse of chatHistory.ts found no heading"
    return (pinned.group(1), *re.findall(r'"([^"]+)"', dates.group(1)))


def test_the_list_headings_are_the_headings_of_the_chat_list() -> None:
    """Visual review P3: one list of headings, pinned to the TypeScript.
    Mutation: add a day group to ``DATE_GROUPS`` and not to ``LIST_HEADINGS``
    -> red here."""
    from gateway.chat_topic import LIST_HEADINGS

    assert _list_headings_of_the_control_plane() == LIST_HEADINGS


def test_the_heading_reader_can_fail() -> None:
    src = ('export const PINNED_GROUP = "Pinned" as const;\n'
           'export const DATE_GROUPS = ["Today", "Soon"] as const;')
    pinned = re.search(r'export const PINNED_GROUP = "([^"]+)" as const;', src)
    dates = re.search(r"export const DATE_GROUPS = \[([^\]]+)\] as const;", src)
    assert pinned and dates
    assert (pinned.group(1), *re.findall(r'"([^"]+)"', dates.group(1))) == ("Pinned", "Today", "Soon")


@pytest.mark.parametrize("heading", ["Pinned", "Today", "Yesterday", "Previous 7 days",
                                     "Previous 30 days", "Older"])
def test_a_name_of_a_list_heading_is_refused(heading: str) -> None:
    """Visual review P3: a topic called "Today" would read as the day group.
    Each heading is refused as it is, in another case, and with other spacing.
    Mutation: drop the heading check of ``clean_name`` -> red."""
    from gateway.chat_topic import TopicNameError, clean_name

    for raw in (heading, heading.upper(), heading.lower(), f"  {heading} ",
                heading.replace(" ", "  ")):
        with pytest.raises(TopicNameError, match=r"^The chat list uses this name\. Pick another"):
            clean_name(raw)


@pytest.mark.parametrize("raw", ["Today plan", "Pinned notes", "Older clients", "Previous 8 days"])
def test_a_name_that_only_holds_a_heading_passes(raw: str) -> None:
    from gateway.chat_topic import clean_name

    assert clean_name(raw) == raw


def test_the_migration_check_agrees_with_the_cap() -> None:
    from gateway.chat_topic import NAME_MAX

    sql = _migration().read_text(encoding="utf-8")
    assert f"char_length(name) BETWEEN 1 AND {NAME_MAX}" in sql
    assert "name = btrim(name)" in sql
    assert "name !~ '[[:cntrl:]]'" in sql


# ── chat-topic-seed ─────────────────────────────────────────────────────────


def _center_teams() -> list[str]:
    """The ``name`` of each Center in ``centers.ts``, without " Center"."""
    src = (ROOT / "workbench" / "control_plane" / "src" / "lib" / "centers.ts").read_text(
        encoding="utf-8")
    body = src[src.index("export const CENTERS"):]
    names = re.findall(r'^    name: "([^"]+)",$', body, re.M)
    assert names, "the parse of centers.ts found no Center"
    for n in names:
        assert n.endswith(" Center"), n
    return [n[: -len(" Center")] for n in names]


def test_the_teams_are_the_centers_of_the_control_plane() -> None:
    from gateway.chat_topic import TEAMS

    assert list(TEAMS) == _center_teams()


def test_the_seed_is_each_team_then_general() -> None:
    from gateway.chat_topic import GENERAL, TEAMS, seed_names

    assert seed_names() == (*TEAMS, GENERAL)
    assert seed_names(["Sales", " sales ", "Ops"]) == ("Sales", "Ops", "General")
    assert seed_names(["General"]) == ("General",)


def test_the_seed_is_pure() -> None:
    """CO-2a runs no seed: no SQL, no session, and no other module calls it."""
    import inspect

    from gateway import chat_topic as store

    src = inspect.getsource(store.seed_names)
    for word in ("text(", "execute", "session", "INSERT"):
        assert word not in src, word
    callers = [p for p in _py_files() if p != STORE
               and "seed_names" in p.read_text(encoding="utf-8", errors="replace")]
    assert callers == []


# ── chat-topic-dark ─────────────────────────────────────────────────────────


def _topic_orgs(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_topics_orgs", raw, raising=False)


@pytest.mark.parametrize(("raw", "org", "on"), [
    ("*", "org-a", True),
    ("org-b, ORG-A", "org-a", True),
    ("org-b", "org-a", False),
    ("", "org-a", False),
    ("*", None, False),
    ("*", "  ", False),
])
def test_the_one_reader_of_the_switch(monkeypatch, raw: str, org: str | None, on: bool) -> None:
    from gateway.chat_topic import topics_allowed

    _topic_orgs(monkeypatch, raw)
    assert topics_allowed(org) is on


def test_a_fault_of_the_settings_reads_as_off(monkeypatch) -> None:
    import acb_common
    from gateway.chat_topic import topics_allowed

    def _boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert topics_allowed("org-a") is False


def _user(email: str, org: str | None, *, admin: bool = False):
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:chat", *(["admin:settings:manage"] if admin else [])]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org)


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import chat_topics

    app = FastAPI()
    app.include_router(chat_topics.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class _NoDatabase:
    """A ``tenant_session`` that fails the test on use."""

    def __call__(self, *a: Any, **k: Any):
        raise AssertionError("the route opened a session")


_TID = "11111111-2222-3333-4444-555555555555"
_REQUESTS = (
    ("GET", "/chat/topics", None),
    ("POST", "/chat/topics", {"name": "Board prep"}),
    ("PATCH", f"/chat/topics/{_TID}", {"name": "Board prep"}),
    ("POST", f"/chat/topics/{_TID}/merge", {"into": str(uuid.UUID(int=7))}),
)


def test_each_route_answers_404_with_the_switch_off_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "org-b")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    for method, path, body in _REQUESTS:
        res = client.request(method, path, json=body)
        assert res.status_code == 404, (method, path, res.text)
        assert res.json() == {"detail": "Not Found"}


def test_with_the_switch_on_the_same_requests_reach_the_store(monkeypatch) -> None:
    """The twin of the 404 test: the routes exist, and only the switch hides them."""
    from gateway import chat_topic as store
    from gateway.routes import chat_topics

    reached: list[str] = []
    topic = store.Topic(id=_TID, name="Board prep", scope="company", hidden_at=None)

    def _list(org, member, with_hidden):
        reached.append("list")
        return [topic]

    def _create(org, member, name):
        reached.append("create")
        return topic

    def _patch(org, member, tid, *, name, hidden, admin):
        reached.append("patch")
        return store.Change("ok", topic), True

    def _merge(org, member, tid, into, admin):
        reached.append("merge")
        return store.Change("ok", topic)

    _topic_orgs(monkeypatch, "org-a")
    for name, fn in (("_list", _list), ("_create", _create), ("_patch", _patch),
                     ("_merge", _merge)):
        monkeypatch.setattr(chat_topics, name, fn)
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    codes = [client.request(m, p, json=b).status_code for m, p, b in _REQUESTS]
    assert codes == [200, 201, 200, 200]
    assert reached == ["list", "create", "patch", "merge"]


def test_the_real_gateway_app_serves_the_four_routes() -> None:
    """``main.py`` registers the router inside ``try``, so a failed import
    answers 404 too. This reads the real app."""
    from gateway.main import app

    from tests.unit._routes import served_routes

    served = {(m, r.path) for r in served_routes(app.routes) for m in (r.methods or ())}
    for pair in (("GET", "/chat/topics"), ("POST", "/chat/topics"),
                 ("PATCH", "/chat/topics/{topic_id}"),
                 ("POST", "/chat/topics/{topic_id}/merge")):
        assert pair in served, pair


def test_the_switch_is_the_first_dependency_of_the_router() -> None:
    from gateway.routes import chat_topics

    first = chat_topics.router.dependencies[0]
    assert first.dependency is chat_topics.require_topics


def test_a_caller_with_no_address_is_refused_before_any_sql(monkeypatch) -> None:
    """The feature gate of the chat answers 401 first. ``_member`` answers 403
    behind it, so a shared key such as ``default`` never keys one list."""
    import acb_graph
    from fastapi import HTTPException
    from gateway.routes.chat_topics import NO_ADDRESS, _member

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("", "org-a"))
    for method, path, body in _REQUESTS:
        code = client.request(method, path, json=body).status_code
        assert code in (401, 403), (method, path, code)
    for email in ("", "   "):
        with pytest.raises(HTTPException) as refused:
            _member(_user(email, "org-a"))
        assert (refused.value.status_code, refused.value.detail) == (403, NO_ADDRESS)
    assert _member(_user(" ana@co2.test ", "org-a")) == "ana@co2.test"


@pytest.mark.parametrize("bad", ["abc", "1%3B%20DROP%20TABLE%20x", "00000000"])
def test_an_id_that_is_not_a_uuid_is_404_before_any_sql(monkeypatch, bad: str) -> None:
    import acb_graph
    from gateway.routes.chat_topics import NOT_FOUND

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    res = client.patch(f"/chat/topics/{bad}", json={"name": "x"})
    assert (res.status_code, res.json()["detail"]) == (404, NOT_FOUND)
    res = client.post(f"/chat/topics/{_TID}/merge", json={"into": bad})
    assert (res.status_code, res.json()["detail"]) == (404, NOT_FOUND)


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "a\nb"])
def test_a_bad_name_is_422_before_any_sql(monkeypatch, name: str) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a"))
    assert client.post("/chat/topics", json={"name": name}).status_code == 422
    assert client.patch(f"/chat/topics/{_TID}", json={"name": name}).status_code == 422


def test_an_empty_patch_is_422_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    assert _client(_user("ana@co2.test", "org-a")).patch(
        f"/chat/topics/{_TID}", json={}).status_code == 422


_BAD_BODIES = ("{not json", "", "[1, 2]", '"a string"', "null")


def _raw(client, method: str, path: str, body: str):
    return client.request(method, path, content=body.encode("utf-8"),
                          headers={"content-type": "application/json"})


def test_a_bad_body_with_the_switch_off_is_404(monkeypatch) -> None:
    """Verifier P1: FastAPI parses a declared body before any dependency, so a
    body that is not JSON got 422 from a dark route. No route declares a body
    now, and the switch answers 404 first. Mutation: declare ``payload:
    TopicIn`` on ``create_topic`` again -> red."""
    import acb_graph

    _topic_orgs(monkeypatch, "org-b")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    for method, path, _body in _REQUESTS[1:]:
        for body in _BAD_BODIES:
            res = _raw(client, method, path, body)
            assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), (
                method, path, body, res.text)


def test_a_bad_query_with_the_switch_off_is_404(monkeypatch) -> None:
    """The same class as the body: a query value that FastAPI cannot parse
    must not answer 422 from a dark route."""
    import acb_graph

    _topic_orgs(monkeypatch, "org-b")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    res = _client(_user("ana@co2.test", "org-a", admin=True)).get(
        "/chat/topics?include_hidden=maybe")
    assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), res.text


def test_a_bad_body_with_the_switch_on_is_422_before_any_sql(monkeypatch) -> None:
    import acb_graph
    from gateway.routes.chat_topics import BAD_BODY

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    for method, path, _body in _REQUESTS[1:]:
        for body in _BAD_BODIES:
            res = _raw(client, method, path, body)
            assert (res.status_code, res.json()) == (422, {"detail": BAD_BODY}), (
                method, path, body, res.text)


def test_no_topic_route_declares_a_body_parameter() -> None:
    """The source half of the test above: a declared body runs before the
    switch. Each body is read by ``_body`` in the handler."""
    from gateway.routes import chat_topics

    for route in chat_topics.router.routes:
        assert route.dependant.body_params == [], route.path


# ── chat-topic-session-identity ─────────────────────────────────────────────


@pytest.mark.parametrize("extra", [
    {"owner_email": "bob@co2.test"}, {"scope": "company"},
    {"organization_id": str(uuid.UUID(int=9))}, {"member_email": "bob@co2.test"},
])
def test_a_body_that_names_an_owner_scope_or_org_is_422(monkeypatch, extra) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    assert client.post("/chat/topics", json={"name": "x", **extra}).status_code == 422
    assert client.patch(f"/chat/topics/{_TID}", json={"name": "x", **extra}).status_code == 422


def test_the_member_and_the_org_never_come_from_the_request(monkeypatch) -> None:
    """R11: the routes take the address and the org of the session. A query
    parameter or a header that names another member changes nothing."""
    from gateway.routes import chat_topics

    seen: list[tuple[str | None, str, bool]] = []

    def _list(org, member, with_hidden):
        seen.append((org, member, with_hidden))
        return []

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(chat_topics, "_list", _list)
    res = _client(_user("ana@co2.test", "org-a")).get(
        "/chat/topics?member_email=bob@co2.test&organization_id=org-b&include_hidden=true",
        headers={"X-User-Email": "bob@co2.test", "X-Organization-Id": "org-b"})
    assert res.status_code == 200, res.text
    # A member who is not an admin never reads a hidden topic.
    assert seen == [("org-a", "ana@co2.test", False)]
    assert res.json() == {"topics": [], "can_manage": False}


def test_an_admin_asks_for_the_hidden_topics(monkeypatch) -> None:
    from gateway.routes import chat_topics

    seen: list[bool] = []
    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(chat_topics, "_list", lambda o, m, h: seen.append(h) or [])
    client = _client(_user("ana@co2.test", "org-a", admin=True))
    assert client.get("/chat/topics?include_hidden=true").json()["can_manage"] is True
    assert client.get("/chat/topics").status_code == 200
    assert seen == [True, False]


@pytest.mark.parametrize(("outcome", "code"), [
    ("missing", 404), ("forbidden", 403), ("duplicate", 409), ("not_company", 422),
    ("same", 422),
])
def test_each_outcome_has_its_answer(monkeypatch, outcome: str, code: int) -> None:
    from gateway import chat_topic as store
    from gateway.routes import chat_topics

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(chat_topics, "_patch",
                        lambda *a, **k: (store.Change(outcome), False))  # type: ignore[arg-type]
    res = _client(_user("ana@co2.test", "org-a")).patch(
        f"/chat/topics/{_TID}", json={"name": "Board prep"})
    assert res.status_code == code, res.text


# ── chat-topic-expand-only ──────────────────────────────────────────────────


def test_the_migration_creates_one_table_and_changes_nothing_else() -> None:
    sql = _migration().read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", code) == [TABLE]
    assert "SET LOCAL lock_timeout" in code
    assert re.search(r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\)", code)
    assert "UNIQUE (organization_id, id)" in code
    assert "CHECK (scope IN ('company', 'member'))" in code
    assert ("ON chat_topic (organization_id, scope, COALESCE(owner_email, ''), lower(name))"
            in code)
    assert "FORCE  ROW LEVEL SECURITY" in code
    assert "WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)" in code
    for word in ("DROP TABLE", "RENAME", "ALTER TABLE chat_", "DELETE FROM", "UPDATE ",
                 "INSERT INTO", "REFERENCES chat_"):
        assert word not in code, word


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_topic\.sql", _migration().name)
