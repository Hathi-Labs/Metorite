"""WS-61 KB-3c — the Knowledge embed sweep and the member of an embed, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §6.4 and §13.

* **Off means no work.** With ``KNOWLEDGE_EMBED_SWEEP`` off, the loop does
  not start. With no org on ``KNOWLEDGE_EMBED``, a tick reads no org list,
  no chunk and calls no door. An org off the list is never read.
* **The bound.** One tick asks for at most ``MAX_CHUNKS`` chunks of each org.
* **A member is bound.** The sweep binds the page's author, or else the
  member of its newest version, as a PROVEN member. A member who left the
  org never pays. ``embed_pending`` binds the acting member of the write,
  also when the request bound nobody, and also over an inherited member.
* **No page text in a log line**, and no member address either.
* **The kill switch has one reader**, inside ``start_kb_embed_sweep``.

The SQL is proved against a real database in ``test_kb_search_r8.py``.

Mutations this file catches (R7):

* the ``job_member_scope`` leaves the sweep -> ``TestTheMember``;
* the ``job_member_scope`` leaves ``embed_pending`` -> ``TestTheJobMember``;
* the ``knowledge_embed_named`` check leaves ``run_tick`` -> ``TestOffMeansNoWork``.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.kb_index.sweep", reason="gateway not installed")

from acb_common import get_settings
from acb_common._log import bind_run_context, clear_run_context
from gateway.kb_index import embed as embed_mod
from gateway.kb_index import index as index_mod
from gateway.kb_index import sweep

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps/services/gateway/gateway/main.py"

ORG = "44444444-4444-4444-4444-444444444444"
OTHER = "55555555-5555-5555-5555-555555555555"
AUTHOR = "author@kb3c.test"
EDITOR = "editor@kb3c.test"
SECRET = "The vault code is BLUEFIN-7731"


def _flags(monkeypatch: pytest.MonkeyPatch, embed_orgs: str, *, sweep_on: bool = True) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "knowledge_embed", embed_orgs, raising=False)
    monkeypatch.setattr(settings, "knowledge_embed_sweep", sweep_on, raising=False)


def _waiting(cid: int, page: str = "page-1", text: str = SECRET,
             members: tuple[str, ...] = (AUTHOR, EDITOR)) -> sweep.Waiting:
    return sweep.Waiting(
        pending=index_mod.Pending(id=cid, content_sha=f"sha-{cid}", text=text),
        page_id=page, members=members,
    )


class Door:
    """A stand-in for ``embed.embed``. It records the texts and the member."""

    def __init__(self, answer: str = "vector") -> None:
        self.calls: list[list[str]] = []
        self.members: list[dict[str, Any]] = []
        self.answer = answer

    async def __call__(self, texts: list[str], **kw: Any) -> list[list[float] | None]:
        from acb_llm.routed import run_attribution

        self.calls.append(list(texts))
        self.members.append(run_attribution())
        if self.answer == "none":
            return [None] * len(texts)
        return [[0.1] * embed_mod.EMBED_DIMS for _ in texts]


class Store:
    """Stand-ins for the two reads and the write of the sweep."""

    def __init__(self, found: list[sweep.Waiting]) -> None:
        self.found = found
        self.limits: list[int] = []
        self.skips: list[list[int]] = []
        self.orgs_read = 0
        self.selects: list[str] = []
        self.written: list[tuple[str, list[int]]] = []

    async def organization_ids(self) -> list[str]:
        self.orgs_read += 1
        return [ORG, OTHER]

    async def select(self, org: str, limit: int, skip: list[int]) -> list[sweep.Waiting]:
        self.selects.append(org)
        self.limits.append(limit)
        self.skips.append(list(skip))
        return [w for w in self.found if w.pending.id not in skip][:limit]

    async def write(self, org: str, rows: list[tuple[Any, list[float]]]) -> int:
        self.written.append((org, [p.id for p, _ in rows]))
        return len(rows)


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch):
    """The sweep with its database and its door replaced. The door is a ``Door``."""
    sweep._waiting.clear()
    sweep._stopping.clear()
    door = Door()
    store = Store([_waiting(1), _waiting(2), _waiting(3, page="page-2")])
    departed: dict[str, str | None] = {}

    async def fake_departed(org: str, member: str) -> str | None:
        return departed.get(member)

    import gateway.my_day_loop as my_day_loop

    monkeypatch.setattr(sweep, "_organization_ids", store.organization_ids)
    monkeypatch.setattr(sweep, "select_waiting", store.select)
    monkeypatch.setattr(index_mod, "write_vectors", store.write)
    monkeypatch.setattr(embed_mod, "embed", door)
    monkeypatch.setattr(my_day_loop, "departed", fake_departed)
    clear_run_context()
    yield door, store, departed
    sweep._waiting.clear()
    clear_run_context()


def _tick() -> sweep.TickResult:
    return asyncio.run(sweep.run_tick())


# ── Off means no work ───────────────────────────────────────────────────────

class TestOffMeansNoWork:
    def test_the_switch_off_starts_no_loop(self, monkeypatch, wired):
        _flags(monkeypatch, "*", sweep_on=False)

        async def go() -> bool:
            started = await sweep.start_kb_embed_sweep()
            assert not sweep.loop_running()
            return started

        assert asyncio.run(go()) is False

    def test_the_switch_defaults_on(self):
        assert type(get_settings()).model_fields["knowledge_embed_sweep"].default is True

    def test_no_org_on_knowledge_embed_reads_nothing(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, "")
        result = _tick()
        assert store.orgs_read == 0 and store.selects == []
        assert door.calls == [] and store.written == []
        assert result.orgs == 0 and result.chunks == 0

    def test_knowledge_off_reads_nothing_also_with_star(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, "*")
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False, raising=False)
        _tick()
        assert store.orgs_read == 0 and door.calls == []

    def test_an_org_off_the_list_is_never_read(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, ORG)
        result = _tick()
        assert store.selects == [ORG], "the other org was read"
        assert result.orgs == 1
        assert door.calls, "non-vacuity: the listed org was embedded"


# ── The bound ───────────────────────────────────────────────────────────────

class TestTheBound:
    def test_one_tick_asks_for_at_most_max_chunks(self, monkeypatch, wired):
        _door, store, _ = wired
        _flags(monkeypatch, "*")
        _tick()
        assert sweep.MAX_CHUNKS == 128
        assert store.limits == [sweep.MAX_CHUNKS, sweep.MAX_CHUNKS]

    def test_one_door_call_for_each_page(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, ORG)
        result = _tick()
        assert sorted(len(c) for c in door.calls) == [1, 2]
        assert sorted(i for _, ids in store.written for i in ids) == [1, 2, 3]
        assert result.chunks == 3 and result.written == 3

    def test_a_refused_chunk_waits_and_does_not_hold_the_tick(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, ORG)
        store.found = [_waiting(9, page="lonely")]
        door.answer = "none"
        assert _tick().refused == 1
        _tick()
        assert store.skips[-1] == [9], "the refused chunk was asked again at once"
        assert len(door.calls) == 1

    def test_an_outage_parks_nothing(self, monkeypatch, wired):
        door, store, _ = wired
        _flags(monkeypatch, ORG)
        door.answer = "none"
        result = _tick()
        assert result.outages == 1 and store.written == []
        assert sweep._waiting == {}, "an outage parked a chunk"


# ── A member is bound ───────────────────────────────────────────────────────

class TestTheMember:
    def test_the_sweep_binds_the_author_as_a_proven_member(self, monkeypatch, wired):
        door, _store, _ = wired
        _flags(monkeypatch, ORG)
        _tick()
        assert door.members, "non-vacuity: the door was called"
        for seen in door.members:
            assert seen["member"] == AUTHOR and seen["member_proven"] is True
            assert seen["module_slug"] == "knowledge"

    def test_an_author_who_left_hands_over_to_the_editor(self, monkeypatch, wired):
        door, _store, departed = wired
        _flags(monkeypatch, ORG)
        departed[AUTHOR] = "status"
        _tick()
        assert {m["member"] for m in door.members} == {EDITOR}

    def test_no_active_member_means_no_call(self, monkeypatch, wired):
        door, store, departed = wired
        _flags(monkeypatch, ORG)
        departed[AUTHOR] = "other_org"
        departed[EDITOR] = "no_org"
        result = _tick()
        assert door.calls == [] and store.written == []
        assert result.no_member == 3

    def test_a_failed_identity_read_pays_nothing(self, monkeypatch, wired):
        door, _store, _ = wired
        _flags(monkeypatch, ORG)

        async def broken(org: str, member: str) -> str | None:
            raise RuntimeError("identity read failed")

        import gateway.my_day_loop as my_day_loop

        monkeypatch.setattr(my_day_loop, "departed", broken)
        _tick()
        assert door.calls == []

    def test_the_tick_does_not_leak_the_member(self, monkeypatch, wired):
        from acb_common._log import get_run_context

        _flags(monkeypatch, ORG)
        _tick()
        assert "user" not in get_run_context()


class TestTheJobMember:
    """Item 4 of KB-3c: the request-time job binds the acting member."""

    def _run(self, monkeypatch, *, member: str | None, inherited: str | None = None) -> Door:
        _flags(monkeypatch, ORG)
        door = Door()
        monkeypatch.setattr(embed_mod, "embed", door)

        async def select(org: str, page: str, shas: Any) -> list[Any]:
            return [index_mod.Pending(id=1, content_sha="s", text="words")]

        async def write(org: str, rows: list[Any]) -> int:
            return len(rows)

        monkeypatch.setattr(index_mod, "select_pending", select)
        monkeypatch.setattr(index_mod, "write_vectors", write)

        async def go() -> int:
            clear_run_context()
            if inherited:
                bind_run_context(user=inherited, member_verified=True)
            return await index_mod.embed_pending(ORG, "page-1", ("s",), member)

        assert asyncio.run(go()) == 1
        clear_run_context()
        return door

    def test_the_job_binds_the_acting_member(self, monkeypatch):
        door = self._run(monkeypatch, member=AUTHOR)
        assert door.members[0]["member"] == AUTHOR
        assert door.members[0]["member_proven"] is True

    def test_the_acting_member_beats_an_inherited_one(self, monkeypatch):
        door = self._run(monkeypatch, member=AUTHOR, inherited="bystander@kb3c.test")
        assert door.members[0]["member"] == AUTHOR

    def test_no_shas_means_no_call(self, monkeypatch):
        _flags(monkeypatch, ORG)
        door = Door()
        monkeypatch.setattr(embed_mod, "embed", door)
        assert asyncio.run(index_mod.embed_pending(ORG, "page-1", (), AUTHOR)) == 0
        assert door.calls == []


# ── No page text in a log line ──────────────────────────────────────────────

class TestTheLog:
    def test_no_page_text_and_no_member_in_any_line(self, monkeypatch, wired):
        _flags(monkeypatch, ORG)
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

        monkeypatch.setattr(sweep, "_log", _Log())
        _tick()
        assert [e for e, _ in lines] == ["knowledge.embed_sweep.tick"]
        kw = lines[0][1]
        assert kw["orgs"] == 1 and kw["chunks"] == 3 and kw["written"] == 3
        flat = repr(lines)
        assert "BLUEFIN" not in flat and AUTHOR not in flat and EDITOR not in flat

    def test_a_failed_org_logs_the_error_type_only(self, monkeypatch, wired):
        _flags(monkeypatch, ORG)
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                lines.append((event, kw))

        async def boom(org: str, limit: int, skip: list[int]) -> list[Any]:
            raise RuntimeError(SECRET)

        monkeypatch.setattr(sweep, "_log", _Log())
        monkeypatch.setattr(sweep, "select_waiting", boom)
        _tick()
        assert ("knowledge.embed_sweep.org_failed", {"error": "RuntimeError"}) in lines
        assert "BLUEFIN" not in repr(lines)


# ── The kill switch has one reader ──────────────────────────────────────────

class TestTheLifespan:
    def test_main_starts_and_stops_the_sweep(self):
        src = MAIN.read_text(encoding="utf-8")
        assert "start_kb_embed_sweep" in src and "stop_kb_embed_sweep" in src

    def test_main_does_not_read_the_switch(self):
        src = MAIN.read_text(encoding="utf-8")
        assert "knowledge_embed_sweep_enabled" not in src
        assert ".knowledge_embed_sweep" not in src
        assert "sweep_enabled(" not in src

    def test_the_start_function_reads_the_switch(self):
        tree = ast.parse(Path(sweep.__file__).read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_kb_embed_sweep")
        called = {c.func.id for c in ast.walk(start)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "sweep_enabled" in called
