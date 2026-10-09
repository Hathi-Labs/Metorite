"""A whole WhatsApp chat is read once, and windows that overlap merge. H-279.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.3a (the span rule)
and §9 N4. The rule lives in two places, and in no instruction:

* the shared seam, ``acb_skills/narrowing.py``: ``Narrowed.span`` sends the
  call to ``read_span`` with no PICK, the count line of a span says how many
  older matches were not read, and ``FullItem.covers`` lets one item hold
  several kept ids;
* the adapter, ``agent-whatsapp-assistant/narrow_source.py``: it sets
  ``span`` only when the filters alone chose every message of ONE chat, it
  reads that chat in ONE GET of the thread route (the route of
  ``read_whatsapp_chat``, newest first since H-277), and it merges READ
  windows of one chat that share a message.

The tests run the REAL tool, the REAL pipeline and the REAL adapter on a
recorder at the agent's ``_get`` (:class:`Gateway`). PICK is a recorder too
(:func:`_pick_spy`), so a test sees each PICK step that runs. Hermetic: the
adapter sends no SQL, and the thread route does not change, so R8 binds
nothing here. ``test_whatsapp_thread_newest.py`` holds the route on Postgres.

Mutations this file catches (R7), each one run red before the change:

* a whole chat goes through PICK, or reads windows ->
  ``test_a_whole_chat_is_one_read_with_no_pick``;
* the count line of a cut span hides the older messages, or names no next
  step -> ``test_the_count_line_states_the_cap`` and
  ``test_an_overflow_says_more_than``;
* a span claims a message that its read did not hold, or a block cuts a line
  -> ``test_a_span_counts_only_what_it_read`` and
  ``test_a_long_chat_splits_into_whole_blocks``;
* a part of a chat reads as a whole chat -> ``test_a_part_of_a_chat_is_not_a_span``;
  more than 200 matches with no ``chat_id`` read as one chat ->
  ``test_an_overflow_with_no_chat_id_is_not_a_span`` (review P1);
* the seam keeps the oldest blocks of a span past the READ cap ->
  ``test_the_seam_keeps_the_newest_blocks``;
* a failed span read loses the matches -> ``test_a_failed_span_read_falls_back``;
* two windows that overlap show a message twice, or a merged block cuts a
  kept line -> ``test_windows_that_overlap_merge`` and
  ``test_a_long_merge_keeps_the_windows_apart``;
* the tool counts a covered id as a failed read -> ``test_a_covered_id_counts_as_read``.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from acb_skills import narrowing
from acb_skills.narrowing import Candidate, FullItem, Narrowed, Verdict

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "apps" / "agents" / "agent-whatsapp-assistant"
QUERY = "Summarise what the Dealers North group said about the October stock."


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "h279_whatsapp_narrow_source", AGENT_DIR / "narrow_source.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ns = _load()


def _id(*parts: Any) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "h279/" + "/".join(map(str, parts))))


GROUP = _id("chat", "dealers-north")
OTHER = _id("chat", "dealers-south")
DM = _id("chat", "asha")


def _rows(chat: str, n: int, *, name: str = "Dealers North", kind: str = "group",
          text: str = "October stock line {k}", start_day: int = 1) -> list[dict[str, Any]]:
    """*n* messages of one chat, oldest first, one hour apart."""
    out = []
    for k in range(n):
        day, hour = start_day + k // 20, k % 20
        out.append({
            "id": _id(chat, k), "chat_id": chat, "wa_message_id": f"test-{chat[:4]}-{k}",
            "direction": "in", "kind": "text",
            "sender_name": "Asha Menon" if k % 2 else "Dev Malhotra",
            "body_text": text.format(k=k), "transcript_text": None,
            "sent_at": f"2026-09-{day:02d}T{hour + 2:02d}:00:00+00:00",
            "chat_name": name, "chat_kind": kind,
        })
    return out


class Gateway:
    """A recorder at the agent's ``_get``. It serves the search and the thread
    route over :attr:`rows`, with the filters and the order of the real routes."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_span = False
        self.drop_from_span: set[str] = set()

    def _search(self, p: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = list(self.rows)
        if p.get("chat_id"):
            rows = [r for r in rows if r["chat_id"] == p["chat_id"]]
        contact = str(p.get("contact") or "").lower()
        if contact:
            rows = [r for r in rows if contact in r["chat_name"].lower()
                    or contact in r["sender_name"].lower()]
        if p.get("chat_kind"):
            rows = [r for r in rows if r["chat_kind"] == p["chat_kind"]]
        if p.get("direction"):
            rows = [r for r in rows if r["direction"] == p["direction"]]
        if p.get("sent_after"):
            rows = [r for r in rows if r["sent_at"] >= p["sent_after"]]
        if p.get("sent_before"):
            rows = [r for r in rows if r["sent_at"] <= p["sent_before"]]
        words = [w.lower() for w in str(p.get("q") or "").split() if w != "OR"]
        if words:
            rows = [r for r in rows if set(words) & set(r["body_text"].lower().split())]
        rows.sort(key=lambda r: r["sent_at"], reverse=True)
        return rows[:int(p.get("limit") or 50)]

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        p = dict(params or {})
        self.calls.append((path, p))
        if path == ns.SEARCH_PATH:
            return self._search(p)
        prefix, suffix = "/whatsapp/chats/", "/messages"
        assert path.startswith(prefix) and path.endswith(suffix), path
        chat = path[len(prefix):-len(suffix)]
        thread = sorted((r for r in self.rows if r["chat_id"] == chat),
                        key=lambda r: (r["sent_at"], r["id"]))
        if "around" not in p:
            if self.fail_span:
                raise RuntimeError("WhatsApp GET failed (503)")
            newest = thread[-int(p["limit"]):]
            return [r for r in newest if r["id"] not in self.drop_from_span]
        at = next(i for i, r in enumerate(thread) if r["id"] == p["around"])
        w = int(p["window"])
        return thread[max(0, at - w):at + w + 1]

    @property
    def reads(self) -> list[tuple[str, dict[str, Any]]]:
        return [(path, p) for path, p in self.calls if path != ns.SEARCH_PATH]


@pytest.fixture
def picks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """PICK as a recorder: each call adds its candidate count, and every
    candidate gets a sure ``yes``. A margin of 0 makes PICK run on every call
    that is not a span, so a span that falls into PICK shows here."""
    seen: list[int] = []

    async def _pick_spy(query: str, candidates: Sequence[Candidate]) -> Any:
        seen.append(len(candidates))
        return narrowing._Picked(verdicts=[Verdict("yes", 0.95)] * len(candidates))

    monkeypatch.setattr(narrowing, "_pick", _pick_spy)
    monkeypatch.setattr(narrowing, "PICK_MARGIN", 0.0)
    return seen


def _tool(gateway: Gateway) -> Any:
    return narrowing.make_narrow_tool(ns.WhatsAppNarrowSource(get=gateway.get))


async def _ask(gateway: Gateway, filters: Mapping[str, Any], query: str = QUERY) -> str:
    return await _tool(gateway)(query, json.dumps(dict(filters)))


def _kept_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith(">> [")]


WHOLE = {"contact": "Dealers North", "group": True, "words": ""}


# ── (a) one whole chat: one READ, no PICK ───────────────────────────────────


@pytest.mark.parametrize("filters", [
    WHOLE,
    {"chat_id": GROUP, "words": ""},
    {"contact": "dealers north", "after": "2026-09-01", "words": ""},
])
async def test_a_whole_chat_is_one_read_with_no_pick(
    picks: list[int], filters: dict[str, Any],
) -> None:
    """27 messages, more than the READ cap of 25. Before H-279, PICK ran and
    25 windows overlapped. Now ONE GET of the thread route reads them, with
    ``limit`` and no ``around``, and every message shows once."""
    rows = _rows(GROUP, 27)
    gateway = Gateway(rows + _rows(DM, 5, name="Asha Menon (Northfield Pumps)", kind="dm"))
    out = await _ask(gateway, filters)
    assert picks == []
    assert gateway.reads == [(ns.THREAD_PATH.format(chat_id=GROUP), {"limit": "27"})]
    assert out.splitlines()[0] == (
        "Read all 27 matches in full, as one span in reading order, with no PICK step. "
        "The filters chose every item of one span, so no item was checked. "
        "Say that you read them all.")
    assert out.count("--- item ") == 1
    assert "To read the older messages" not in out  # a full span needs no next step
    kept = _kept_lines(out)
    assert len(kept) == 27
    for r in rows:  # recall: every match is a kept line, once, with its text
        assert sum(ln.endswith(": " + r["body_text"]) for ln in kept) == 1, r["body_text"]
    # Reading order, oldest first.
    assert kept[0].endswith("October stock line 0") and kept[-1].endswith("October stock line 26")


async def test_the_count_line_states_the_cap(picks: list[int]) -> None:
    """140 matches: the read takes the newest 100 (``SPAN_LIMIT``). The count
    line says that 40 older matches were not read, and how to read them."""
    rows = _rows(GROUP, 140)
    gateway = Gateway(rows)
    out = await _ask(gateway, WHOLE)
    lines = out.splitlines()
    assert picks == []
    assert gateway.reads == [(ns.THREAD_PATH.format(chat_id=GROUP), {"limit": str(ns.SPAN_LIMIT)})]
    assert lines[0] == (
        "Read the newest 100 of 140 matches in full, as one span in reading order, with "
        "no PICK step. The filters chose every item of one span, so no item was checked. "
        "40 older matches were not read.")
    assert lines[1] == (
        f'To read the older messages, call read_whatsapp_chat with chat_id="{GROUP}" '
        f"and limit={140 + ns.SPAN_SLACK}.")
    kept = "\n".join(_kept_lines(out))
    assert all(r["body_text"] + "\n" in kept + "\n" for r in rows[40:])
    assert not any(f"October stock line {k}\n" in kept + "\n" for k in range(40))


async def test_an_overflow_says_more_than(picks: list[int]) -> None:
    """More than 200 matches of the chat that ``chat_id`` names: no total is
    known, so the line says "more than"."""
    gateway = Gateway(_rows(GROUP, 230))
    out = await _ask(gateway, {"chat_id": GROUP, "words": ""})
    lines = out.splitlines()
    assert lines[0].startswith("Read the newest 100 of more than 200 matches in full, as one span")
    assert lines[0].endswith("More than 100 older matches were not read.")
    assert lines[1].endswith(f'chat_id="{GROUP}" and limit={ns.THREAD_LIMIT_MAX}.')
    assert lines[2] == "More than 200 items matched. Narrow the filters to check the rest."
    assert picks == []


@pytest.mark.parametrize("filters", [WHOLE, {"group": True, "words": ""}])
async def test_an_overflow_with_no_chat_id_is_not_a_span(
    picks: list[int], filters: dict[str, Any],
) -> None:
    """Review P1. The newest 200 matches are all in one busy group, and a
    second group holds older matches. Only ``chat_id`` pins ONE chat, so with
    more than 200 matches ``contact`` and ``group`` make no span. The tool
    never says that one chat holds every match."""
    rows = _rows(GROUP, 230, start_day=10) + _rows(OTHER, 5, name="Dealers North East",
                                                   start_day=1)
    gateway = Gateway(rows)
    out = await _ask(gateway, filters)
    assert "as one span" not in out and "read_whatsapp_chat" not in out
    assert picks == [200]
    assert "More than 200 items matched. Narrow the filters to check the rest." in out


class _ManyBlocks:
    """A span source whose read gives more blocks than the READ cap."""

    name = "many"
    filter_keys = frozenset()

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        return Narrowed([Candidate(id=f"m{k}") for k in range(30)], total=30, span=True,
                        span_further="Read further with your other tools.")

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        raise AssertionError("a span takes read_span")

    async def read_span(self, candidates: Sequence[Candidate]) -> list[FullItem]:
        return [FullItem(id=f"m{k}", text=f"block {k}") for k in range(30)]


async def test_the_seam_keeps_the_newest_blocks(picks: list[int]) -> None:
    """The blocks come in reading order, so the tool keeps the LAST 25."""
    out = await narrowing.make_narrow_tool(_ManyBlocks())(QUERY)
    assert out.splitlines()[0].startswith("Read the newest 25 of 30 matches in full")
    assert out.splitlines()[1] == "Read further with your other tools."
    lines = out.splitlines()
    assert "block 29" in lines and "block 5" in lines and "block 4" not in lines
    assert picks == []


async def test_a_span_counts_only_what_it_read(picks: list[int]) -> None:
    """A message that the thread read does not give (for example a row with
    the same time as another) is counted as not read. It is never claimed."""
    rows = _rows(GROUP, 27)
    gateway = Gateway(rows)
    gateway.drop_from_span = {rows[3]["id"]}
    out = await _ask(gateway, WHOLE)
    assert out.splitlines()[0].startswith("Read the newest 26 of 27 matches in full")
    assert out.splitlines()[0].endswith("1 older matches were not read.")
    assert "October stock line 3\n" not in out + "\n"
    assert len(_kept_lines(out)) == 26


async def test_a_long_chat_splits_into_whole_blocks(picks: list[int]) -> None:
    """60 long messages do not fit one body clip. They split into blocks of
    whole lines, each within the clip, and every match still shows."""
    rows = _rows(GROUP, 60, text="October stock line {k} " + "x" * 300)
    gateway = Gateway(rows)
    out = await _ask(gateway, WHOLE)
    assert out.splitlines()[0].startswith("Read all 60 matches in full, as one span")
    blocks = out.split("--- item ")[1:]
    assert len(blocks) > 1
    for block in blocks:
        body = block.split("\n", 2)[2].rstrip("\n")  # after the header and the title
        assert len(body) <= narrowing.BODY_CLIP
    kept = _kept_lines(out)
    assert len(kept) == 60
    assert all(ln.endswith("x" * 300) for ln in kept)  # no line is cut


def test_blocks_keep_the_newest_when_they_must_cut() -> None:
    rows = _rows(GROUP, 30, text="line {k} " + "y" * 1100)
    kept = {r["id"]: ns.item_id(GROUP, r["id"]) for r in rows}
    got = ns.blocks(rows, kept, "Group: Dealers North")
    assert len(got) == 30 // 5  # five lines of about 1140 characters to a block
    assert got[-1].covers[-1] == kept[rows[-1]["id"]]
    many = _rows(GROUP, 200, text="z {k} " + "w" * 1100)
    big = ns.blocks(many, {r["id"]: r["id"] for r in many}, "t")
    assert len(big) == narrowing.READ_CAP
    assert big[-1].covers[-1] == many[-1]["id"]  # the newest block stays


@pytest.mark.parametrize("filters", [
    {"contact": "Dealers North", "group": True, "words": "October"},  # words
    {"contact": "Dealers North", "from_me": False, "words": ""},     # a part
    {"contact": "Dealers North", "has_media": False, "words": ""},   # a part
    {"contact": "Dealers North", "before": "2026-09-30", "words": ""},  # not the newest
    {"contact": "Asha", "words": ""},                                # a sender, not the chat
    {"group": True, "words": ""},                                    # two chats
])
async def test_a_part_of_a_chat_is_not_a_span(picks: list[int], filters: dict[str, Any]) -> None:
    """Only the filters that choose whole chats make a span. Each other case
    reads windows, as before H-279."""
    rows = (_rows(GROUP, 8) + _rows(OTHER, 4, name="Dealers South")
            + _rows(DM, 3, name="Bilal Shah (Bilal Agencies)", kind="dm"))
    gateway = Gateway(rows)
    out = await _ask(gateway, filters)
    assert "as one span" not in out
    assert gateway.reads and all("around" in p for _path, p in gateway.reads)
    assert picks  # PICK ran, because this is not a span


def test_is_whole_chat_reads_each_condition() -> None:
    rows = _rows(GROUP, 3)
    params = ns.search_params(QUERY, WHOLE)
    assert ns.is_whole_chat(WHOLE, params, rows)
    assert not ns.is_whole_chat(WHOLE, {**params, "q": "stock"}, rows)
    assert not ns.is_whole_chat({**WHOLE, "from_me": False}, params, rows)
    assert not ns.is_whole_chat(WHOLE, params, rows + _rows(OTHER, 1))
    assert not ns.is_whole_chat(WHOLE, params, [])
    assert not ns.is_whole_chat({"contact": "Asha", "words": ""},
                                ns.search_params(QUERY, {"contact": "Asha", "words": ""}), rows)
    assert {"account_id", "chat_id", "contact", "group", "after"} == ns.SPAN_KEYS


async def test_a_failed_span_read_falls_back(picks: list[int]) -> None:
    """A span read that fails costs no recall: the tool takes PICK and the
    windows of §3.3 to §3.6, as before H-279."""
    rows = _rows(GROUP, 10)
    gateway = Gateway(rows)
    gateway.fail_span = True
    out = await _ask(gateway, WHOLE)
    assert out.startswith("Checked 10 matches. Kept 10, dropped 0. Read 10 in full.")
    assert picks == [10]
    assert all(r["body_text"] in out for r in rows)


def test_the_span_read_uses_a_real_parameter_of_the_thread_route() -> None:
    """``limit`` is a parameter of the route of ``read_whatsapp_chat``, and the
    next step names the highest value that the route takes."""
    from gateway.routes.whatsapp.transport.messages import list_messages

    limit = inspect.signature(list_messages).parameters["limit"].default
    assert any(getattr(m, "le", None) == ns.THREAD_LIMIT_MAX for m in limit.metadata)
    assert ns.SPAN_LIMIT <= ns.THREAD_LIMIT_MAX


# ── (b) READ windows of one chat that overlap merge ─────────────────────────


async def test_windows_that_overlap_merge(picks: list[int]) -> None:
    """Matches 5 and 7 of one chat have windows 3..7 and 5..9. They merge into
    one block, rows 3 to 9, each once, with both matches marked. Match 20 is
    far away, so its window stays apart."""
    rows = _rows(DM, 30, name="Asha Menon (Northfield Pumps)", kind="dm",
                 text="note {k}")
    for k in (5, 7, 20):
        rows[k]["body_text"] = f"price question {k}"
    gateway = Gateway(rows)
    out = await _ask(gateway, {"words": "price"}, query="Who asked about the price?")
    assert out.startswith("Checked 3 matches. Kept 3, dropped 0. Read 3 in full.")
    assert "read failed" not in out
    assert out.count("--- item ") == 2
    # Match 20 is the newest, so it ranks first. The merged block is last.
    merged = out.split(f"--- item {ns.item_id(DM, rows[7]['id'])}")[1]
    for k in range(3, 10):
        text = rows[k]["body_text"]
        assert sum(ln.endswith(f": {text}") for ln in merged.splitlines()) == 1, text
    assert [ln.split(": ", 1)[1] for ln in _kept_lines(merged)] == [
        "price question 5", "price question 7"]
    every = [ln for ln in out.splitlines() if ln.startswith((">> [", "   ["))]
    assert len(every) == len(set(every))  # no line shows twice


async def test_a_long_merge_keeps_the_windows_apart(picks: list[int]) -> None:
    """A merged block past the body clip would cut a kept line. So the
    windows stay apart, and each kept line is whole."""
    rows = _rows(DM, 12, name="Asha Menon (Northfield Pumps)", kind="dm",
                 text="note {k} " + "q" * 1150)
    for k in (5, 7):
        rows[k]["body_text"] = f"price question {k} " + "p" * 1150
    gateway = Gateway(rows)
    out = await _ask(gateway, {"words": "price"}, query="Who asked about the price?")
    assert out.startswith("Checked 2 matches. Kept 2, dropped 0. Read 2 in full.")
    assert out.count("--- item ") == 2
    kept = _kept_lines(out)
    assert len(kept) == 2 and all(ln.endswith("p" * 1150) for ln in kept)


def test_merge_windows_joins_a_chain() -> None:
    """Window B touches A and C, and A and C do not touch. All three merge."""
    rows = _rows(DM, 20, name="x", kind="dm")
    w = [ns.Window(DM, rows[k]["id"], rows[k - 2:k + 3]) for k in (4, 12, 8)]
    raw = {(DM, x.message_id): ns.item_id(DM, x.message_id) for x in w}
    got = ns.merge_windows(w, raw)
    assert len(got) == 1
    assert got[0].id == ns.item_id(DM, rows[4]["id"])  # the best rank leads
    assert set(got[0].covers) == {ns.item_id(DM, rows[12]["id"]), ns.item_id(DM, rows[8]["id"])}
    assert got[0].text.count("\n") == 12  # 13 lines: rows 2 to 14, each once
    # Another chat never merges, even with the same rows.
    other = ns.Window(OTHER, rows[4]["id"], rows[2:7])
    assert len(ns.merge_windows([w[0], other], {**raw, (OTHER, rows[4]["id"]): "o"})) == 2


# ── The seam: a covered id is read, and is counted once ─────────────────────


class _Covering:
    """An adapter whose READ gives ONE item for two kept ids."""

    name = "covering"
    filter_keys = frozenset()

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        return Narrowed([Candidate(id=i, title=i, size=10) for i in ("a", "b", "c")], total=3)

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        return [FullItem(id="a", text="A and B", covers=("b", "zz")),
                FullItem(id="b", text="B again"), FullItem(id="c", text="C")]


async def test_a_covered_id_counts_as_read(picks: list[int]) -> None:
    out = await narrowing.make_narrow_tool(_Covering())(QUERY)
    assert out.startswith("Checked 3 matches. Kept 3, dropped 0. Read 3 in full.")
    assert "read failed" not in out
    assert "B again" not in out  # b is in the block of a: no text twice
    assert out.count("--- item ") == 2


def test_a_span_with_no_read_span_takes_the_steps_of_pick() -> None:
    """``span`` without ``read_span`` is not a :class:`SpanSource`, so the tool
    takes the normal steps."""
    assert not isinstance(_Covering(), narrowing.SpanSource)
    assert isinstance(ns.WhatsAppNarrowSource(get=None), narrowing.SpanSource)
