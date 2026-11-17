"""The shadow join match of a new chat (WS-CO CO-4, task 3503).

Spec: ``project-docs/specs/chat_organization.md`` §7.1, §7.2, §10 and §11 row
CO-4. Module: ``gateway/chat_join_shadow.py``. Hook: the stream door,
``gateway/routes/agent.py::_shadow_join_first_message``. No database: the R8
half is ``tests/unit/test_chat_join_shadow_r8.py``.

What this proves:

1. :func:`decide` takes each row of §7.1: join, carry, ask and clean.
2. Each bar of §7.2 (and a held chat) turns a sure match into a carry, and
   never a join. The 75 % bar and the 14 days hold at their edge.
3. The same record outranks similar words (D163.4).
4. No column and no argument of the module carries the text of the member.
   Only :func:`fingerprint` takes a text, and it keeps no word. A fake run of
   the whole task sends no word of the first message to any statement.
5. The code lists of the module equal the CHECKs of the migration, and the
   one write names the columns of the migration.
6. Only the module and the member purge name ``chat_join_log`` in Python, and
   no TypeScript file names it.
7. The switch is off by default and fails closed. Off, the task opens no
   session. A failure never raises into the chat.
8. The hook starts the task only for a turn that the member sent, in a chat
   of Mira that is the member's own, with the org and the member of the
   session, and with a fingerprint in place of the words.

Mutations, each red here: let ``decide`` join through a bar; rank by score
before the record hit; add a ``text`` argument to ``record_first_message``;
add a column ``first_message TEXT`` to the migration; drop the
``member_turn`` gate of the hook.
"""

from __future__ import annotations

import ast
import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway import chat_join_shadow as cjs
from gateway.chat_join_shadow import Candidate, decide, fingerprint

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
MODULE = GATEWAY / "chat_join_shadow.py"
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_join_log ("


def _migration() -> Path:
    hits = [p for p in sorted((ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


FIRST = fingerprint("Send the Acme invoice draft to finance and check the totals")


def _c(sid: str = "c1", *, idle_hours: int = 2, words: str = "", record: str = "",
       **flags: Any) -> Candidate:
    return Candidate(session_id=sid, idle_hours=idle_hours, title=fingerprint(words),
                     records=(fingerprint(record),) if record else (), **flags)


# ── 1. The rows of §7.1 ─────────────────────────────────────────────────────


def test_no_candidates_is_clean() -> None:
    d = decide(FIRST, [])
    assert (d.outcome, d.reason, d.candidate_id, d.band) == (
        "clean", "no_candidates", None, "none")


def test_nothing_alike_is_clean() -> None:
    d = decide(FIRST, [_c(words="Holiday rota for March")])
    assert (d.outcome, d.reason, d.candidate_id, d.blocked_by) == (
        "clean", "no_match", None, None)
    assert d.candidate_count == 1


def test_a_strong_match_of_a_recent_chat_joins() -> None:
    d = decide(FIRST, [_c(words="Acme invoice draft finance totals check")])
    assert (d.outcome, d.reason, d.band, d.candidate_id) == (
        "join", "same_words", "strong", "c1")
    assert d.blocked_by is None


def test_an_unsure_match_asks() -> None:
    # 4 of the 7 words of FIRST: a share of 0.57, a score of 0.457.
    d = decide(FIRST, [_c(words="Acme invoice draft finance")])
    assert (d.outcome, d.reason, d.band) == ("ask", "unsure", "unsure")


def test_a_related_match_carries() -> None:
    d = decide(FIRST, [_c(words="Acme invoice and more")])
    assert (d.outcome, d.reason, d.band) == ("carry", "related", "related")


def test_a_strong_match_of_an_older_chat_carries() -> None:
    edge = cjs.JOIN_DAYS * 24
    words = "Acme invoice draft finance totals check"
    assert decide(FIRST, [_c(words=words, idle_hours=edge)]).outcome == "join"
    d = decide(FIRST, [_c(words=words, idle_hours=edge + 1)])
    assert (d.outcome, d.reason, d.idle_hours) == ("carry", "older", edge + 1)


# ── 2. The bars of §7.2 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("flags", "bar"), [
    ({"solo": False}, "not_solo"),
    ({"archived": True}, "archived"),
    ({"live_run": True}, "live_run"),
    ({"open_ask": True}, "open_ask"),
    ({"context_share": 0.75}, "context_full"),
    ({"held": True}, "held"),
    ({"eligible": False}, "not_eligible"),
])
def test_each_bar_carries_and_never_joins(flags: dict[str, Any], bar: str) -> None:
    strong = _c(words="Acme invoice draft finance totals check", **flags)
    d = decide(FIRST, [strong])
    assert (d.outcome, d.reason, d.blocked_by) == ("carry", "blocked", bar)
    unsure = decide(FIRST, [_c(words="Acme invoice draft finance", **flags)])
    assert unsure.outcome == "carry" and unsure.blocked_by == bar
    hit = decide(FIRST, [_c(record="Acme invoice draft", **flags)])
    assert hit.outcome == "carry" and hit.record_hit and hit.blocked_by == bar


def test_the_context_bar_is_at_75_percent() -> None:
    words = "Acme invoice draft finance totals check"
    assert decide(FIRST, [_c(words=words, context_share=0.749)]).outcome == "join"
    assert decide(FIRST, [_c(words=words, context_share=0.75)]).blocked_by == "context_full"


def test_the_bars_are_tested_in_the_order_of_the_list() -> None:
    every = Candidate("c", 1, solo=False, archived=True, live_run=True, open_ask=True,
                      context_share=1.0, held=True, eligible=False)
    assert cjs.first_bar(every) == "not_solo"
    assert cjs.first_bar(Candidate("c", 1)) is None


# ── 3. Records first (D163.4) ───────────────────────────────────────────────


def test_the_same_record_outranks_similar_words() -> None:
    words = _c("words", words="Acme invoice draft finance totals check")
    record = _c("record", record="Acme invoice draft", idle_hours=40)
    d = decide(FIRST, [words, record])
    assert (d.outcome, d.reason, d.candidate_id, d.record_hit) == (
        "join", "same_record", "record", True)
    assert d.score is not None and d.score >= cjs.RECORD_FLOOR
    # The order of the list does not matter.
    assert decide(FIRST, [record, words]).candidate_id == "record"


def test_a_record_of_one_word_is_no_hit() -> None:
    assert not cjs.record_hit(FIRST, _c(record="Acme"))
    assert cjs.record_hit(FIRST, _c(record="Acme invoice"))


def test_the_more_recent_chat_wins_a_tie() -> None:
    words = "Acme invoice draft finance totals check"
    d = decide(FIRST, [_c("old", words=words, idle_hours=50),
                       _c("new", words=words, idle_hours=3)])
    assert d.candidate_id == "new"


# ── 4. No text of the member ────────────────────────────────────────────────


def test_a_fingerprint_keeps_no_word() -> None:
    fp = fingerprint("Zanzibarine quarterly forecast 2026 for the invoices")
    assert fp and all(re.fullmatch(r"[0-9a-f]{16}", h) for h in fp)
    joined = " ".join(fp)
    for word in ("zanzibarine", "quarterly", "forecast", "invoice"):
        assert word not in joined
    # A digit and a stop word never count, and a plural meets its singular.
    assert fingerprint("2026 the for") == frozenset()
    assert fingerprint("invoices") == fingerprint("invoice")
    assert fingerprint(None) == frozenset()


#: Argument names that would carry a text of the member.
_TEXT_ARGS = re.compile(r"(?i)^(message|messages|text|content|words|prompt|sent|body|"
                        r"first_message|query|user_query|title|summary|value)$")


def test_no_argument_of_the_module_carries_a_text_but_the_fingerprint() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    takers: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if any(_TEXT_ARGS.match(a.arg) for a in args):
                takers.append(node.name)
    assert takers == ["fingerprint"], takers


def test_the_task_takes_a_fingerprint_and_no_text() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    sigs = {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ("record_first_message", "schedule_first_message", "decide", "_match", "_run"):
        node = sigs[name]
        first = [a for a in [*node.args.args, *node.args.kwonlyargs] if a.arg == "first"]
        assert first and ast.unparse(first[0].annotation) == "Fingerprint", name


def _columns(sql: str) -> list[str]:
    body = sql.split(_MIGRATION_MARK, 1)[1]
    cols: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(");"):
            break
        m = re.match(r"([a-z_]+)\s+(UUID|TEXT|INTEGER|NUMERIC|BOOLEAN|TIMESTAMPTZ)\b", line)
        if m:
            cols.append(m.group(1))
    return cols


def test_no_column_could_carry_a_text_of_the_member() -> None:
    sql = _migration().read_text(encoding="utf-8")
    cols = _columns(sql)
    assert cols == [
        "organization_id", "session_id", "member_email", "outcome", "candidate_session_id",
        "candidate_count", "score", "band", "record_hit", "candidate_idle_hours", "reason",
        "would_join_blocked_by", "override_kind", "overridden_at", "created_at",
    ]
    for col in cols:
        assert not re.search(r"(?i)message|title|summary|content|words|body|note|text", col)
    # Each TEXT column that is not an id or the member holds a code from a CHECK.
    for code in ("outcome", "band", "reason", "would_join_blocked_by", "override_kind"):
        assert re.search(rf"{code}\s+IN\s*\(|{code} IS NULL\s+OR {code} IN", sql), code


def test_the_code_lists_equal_the_checks_of_the_migration() -> None:
    sql = _migration().read_text(encoding="utf-8")

    def check(name: str) -> tuple[str, ...]:
        block = sql.split(f"CONSTRAINT chat_join_log_{name} CHECK", 1)[1].split("),", 1)[0]
        return tuple(re.findall(r"'([a-z_]+)'", block))

    assert check("outcome") == cjs.OUTCOMES
    assert check("band") == cjs.BANDS
    assert check("reason") == cjs.REASONS
    assert check("blocked_by") == cjs.BARS


def test_the_one_write_names_the_columns_of_the_migration() -> None:
    head = cjs.INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    named = tuple(c.strip() for c in head.split(","))
    assert named == cjs.WRITTEN_COLUMNS
    cols = _columns(_migration().read_text(encoding="utf-8"))
    assert set(named) <= set(cols)
    assert "organization_id" not in named, "the DEFAULT stamps the org"
    assert "ON CONFLICT (session_id) DO NOTHING" in cjs.INSERT_SQL


class _FakeDb:
    """A session that answers each statement of the module from a script, and
    keeps each parameter that it gets."""

    def __init__(self, now: datetime) -> None:
        self.params: list[dict[str, Any]] = []
        self.now = now

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        self.params.append(dict(params or {}))
        if sql == cjs.NEW_CHAT_SQL:
            rows = [SimpleNamespace(id="new")]
        elif sql == cjs.CANDIDATES_SQL:
            rows = [SimpleNamespace(
                id="old", title="Acme invoice draft", updated_at=self.now - timedelta(hours=5),
                solo=True, archived=False, live_run=False, open_ask=False, chars=400,
                eligible=True)]
        elif sql == cjs.RECORDS_SQL:
            rows = [SimpleNamespace(sid="old", title="Acme invoice draft v2")]
        elif sql == cjs.INSERT_SQL:
            rows = [SimpleNamespace(session_id="new")]
        else:
            rows = []
        return SimpleNamespace(first=lambda: rows[0] if rows else None,
                               fetchall=lambda: rows)


def test_a_whole_run_sends_no_word_of_the_first_message(monkeypatch) -> None:
    """The whole task, on a fake session: the switch on, a candidate with a
    record. No parameter of any statement holds a word of the message."""
    from contextlib import asynccontextmanager

    import acb_common.assistant_profile as ap
    import acb_common.db as shared

    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    db = _FakeDb(now)

    @asynccontextmanager
    async def _session(org: str | None = None):
        assert org == "org-a"
        yield db

    async def _switch_on(*_a: Any, **_k: Any) -> bool:
        return True

    async def _listed(*_a: Any, **_k: Any) -> list[Any]:
        return [SimpleNamespace(session_id="old", text="Acme invoice summary",
                                follow_ups=("Check totals",))]

    monkeypatch.setattr(shared, "tenant_session", _session)
    monkeypatch.setattr(ap, "chat_summary_for", lambda org: True)
    monkeypatch.setattr(cjs, "member_switch_on", _switch_on)
    monkeypatch.setattr(cjs, "list_for_member", _listed)
    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: True)
    words = "Zanzibarine invoice draft for Acme"
    d = asyncio.run(cjs.record_first_message(
        "org-a", " Alice@Co4.test ", "new", "turn-1", fingerprint(words), now=now))
    assert d is not None and (d.outcome, d.reason, d.candidate_id) == (
        "join", "same_record", "old")
    seen = repr(db.params).lower()
    for word in ("zanzibarine", "invoice", "draft", "acme"):
        assert word not in seen, word
    insert = db.params[-1]
    assert insert["member"] == "alice@co4.test" and insert["sid"] == "new"
    assert insert["turn"] == "turn-1"


# ── 6. One module names the table ───────────────────────────────────────────


def test_only_the_module_and_the_purge_name_the_table() -> None:
    allowed = {MODULE, GATEWAY / "routes" / "admin" / "members.py"}
    found: list[str] = []
    for root in (ROOT / "apps", ROOT / "packages"):
        for path in root.rglob("*.py"):
            if "tests" in path.parts or path in allowed:
                continue
            if "chat_join_log" in path.read_text(encoding="utf-8", errors="replace"):
                found.append(str(path.relative_to(ROOT)))
    assert not found, found
    src = ROOT / "workbench" / "control_plane" / "src"
    ts = [str(p.relative_to(ROOT)) for p in src.rglob("*.ts*")
          if "node_modules" not in p.parts
          and "chat_join_log" in p.read_text(encoding="utf-8", errors="replace")]
    assert not ts, ts


# ── 7. The switch, and best effort ──────────────────────────────────────────


def test_the_switch_is_off_by_default_and_fails_closed(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_join_shadow_orgs", "", raising=False)
    assert not cjs.shadow_allowed("org-a")
    monkeypatch.setattr(get_settings(), "chat_join_shadow_orgs", "Org-A, org-b", raising=False)
    assert cjs.shadow_allowed("org-a") and cjs.shadow_allowed(" ORG-B ")
    assert not cjs.shadow_allowed("org-c")
    assert not cjs.shadow_allowed(None) and not cjs.shadow_allowed("")
    monkeypatch.setattr(get_settings(), "chat_join_shadow_orgs", "*", raising=False)
    assert cjs.shadow_allowed("any")

    def _boom() -> Any:
        raise RuntimeError("settings")

    import acb_common

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert not cjs.shadow_allowed("any")


def test_off_the_task_opens_no_session(monkeypatch) -> None:
    import acb_common.db as shared

    def _no(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a session opened with the switch off")

    monkeypatch.setattr(shared, "tenant_session", _no)
    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: False)
    assert asyncio.run(cjs.record_first_message(
        "org-a", "a@b.test", "s", "t", FIRST)) is None
    assert asyncio.run(_schedule("org-a")) is None


async def _schedule(org: str) -> Any:
    return cjs.schedule_first_message(org, "a@b.test", "s", "t", FIRST)


def test_a_failure_never_raises(monkeypatch) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: True)
    monkeypatch.setattr(cjs, "_run", _boom)
    assert asyncio.run(cjs.record_first_message("org-a", "a@b.test", "s", "t", FIRST)) is None


def test_a_slow_task_stops_at_its_time(monkeypatch) -> None:
    async def _slow(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: True)
    monkeypatch.setattr(cjs, "_run", _slow)
    monkeypatch.setattr(cjs, "TIMEOUT_S", 0.05)
    assert asyncio.run(cjs.record_first_message("org-a", "a@b.test", "s", "t", FIRST)) is None


def test_no_member_or_no_chat_writes_nothing(monkeypatch) -> None:
    async def _never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("ran")

    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: True)
    monkeypatch.setattr(cjs, "_run", _never)
    assert asyncio.run(cjs.record_first_message("org-a", " ", "s", "t", FIRST)) is None
    assert asyncio.run(cjs.record_first_message("org-a", "a@b.test", "", "t", FIRST)) is None


# ── 8. The hook in the stream door ──────────────────────────────────────────


def _room(**kw: Any) -> Any:
    base = {"resolve_failed": False, "unknown_session": False, "visibility": "private",
            "email": "alice@co4.test", "members": ["alice@co4.test"]}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def scheduled(monkeypatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: org == "org-a")
    monkeypatch.setattr(cjs, "schedule_first_message", lambda *a: calls.append(a))
    return calls


def _hook(room: Any, *, agent: str = "orchestrator", org: str = "org-a",
          member_turn: bool = True, thread: str | None = "t1") -> None:
    from gateway.routes.agent import _shadow_join_first_message

    user = SimpleNamespace(email="alice@co4.test", organization_id=org)
    _shadow_join_first_message(room, thread, agent, user, org, "turn-1",
                               "Zanzibarine invoice for Acme", member_turn=member_turn)


def test_the_hook_starts_the_task_with_the_session_and_a_fingerprint(scheduled) -> None:
    _hook(_room())
    [(org, member, thread, turn, first)] = scheduled
    assert (org, member, thread, turn) == ("org-a", "alice@co4.test", "t1", "turn-1")
    assert first == fingerprint("Zanzibarine invoice for Acme")
    assert "zanzibarine" not in repr(first).lower()


def test_a_chat_with_no_row_yet_counts_as_the_member_s_own(scheduled) -> None:
    _hook(_room(unknown_session=True, members=[]))
    assert len(scheduled) == 1


@pytest.mark.parametrize("case", [
    {"member_turn": False},
    {"agent": "crm-assistant"},
    {"org": "org-b"},
    {"thread": None},
    {"room": None},
    {"room": _room(resolve_failed=True)},
    {"room": _room(visibility="org")},
    {"room": _room(members=["alice@co4.test", "bob@co4.test"])},
])
def test_the_hook_starts_nothing_outside_its_gates(scheduled, case: dict[str, Any]) -> None:
    room = case.pop("room", _room())
    _hook(room, **case)
    assert scheduled == []


def test_the_hook_never_raises(monkeypatch) -> None:
    def _boom(*_a: Any) -> Any:
        raise RuntimeError("loop")

    monkeypatch.setattr(cjs, "shadow_allowed", lambda org: True)
    monkeypatch.setattr(cjs, "schedule_first_message", _boom)
    _hook(_room())


def test_the_stream_door_calls_the_hook_once_after_the_mint() -> None:
    src = (GATEWAY / "routes" / "agent.py").read_text(encoding="utf-8")
    body = src.split("async def run_agent_stream_endpoint(", 1)[1].split("\n@router.", 1)[0]
    assert body.count("_shadow_join_first_message(") == 1
    assert body.index("await _mint_run_row_bounded(") < body.index("_shadow_join_first_message(")
    call = body.split("_shadow_join_first_message(", 1)[1].split("\n    )", 1)[0]
    assert "req.payload" not in call, "the hook takes nothing from the body"
    assert "_room_org" in call and "user" in call
    assert src.count("_shadow_join_first_message(") == 2  # the def and the one call
