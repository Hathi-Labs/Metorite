"""PICK pays for itself, or the tool skips it. H-276.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §3.3a. The rule is ONE
function in the shared seam, ``narrowing.pick_cost``, and never a rule of one
source.

PICK spends about 160 tokens on each candidate. A WhatsApp message is about
15 tokens. So on short items PICK costs more than the READ that it saves.
Before PICK, the tool compares the cost of PICK with the cost of a READ of
every candidate, and it skips PICK when PICK does not save by
``PICK_MARGIN``. A skip reads EVERY candidate, so recall cannot drop. It needs
READ room for each candidate, so it never cuts an item.

The decide requests go through the REAL facade and Console client, on the
:class:`~tests.unit.test_narrowing_pick.Door` transport. Hermetic: no SQL runs
here, so R8 binds nothing.

Mutations this file catches (R7), each one run red before the change:

* the cost check never skips (the code before H-276) ->
  ``test_short_items_skip_pick_and_read_every_one``;
* the cost check skips past the READ cap, or with more matches than the
  candidates -> ``test_more_than_the_read_cap_keeps_pick`` and
  ``test_an_overflow_keeps_pick``;
* the margin goes to 1, or the comparison turns round ->
  ``test_the_margin_is_two_and_decides``;
* a long item, or one of no known size, skips PICK ->
  ``test_long_items_keep_pick`` and ``test_an_unknown_size_keeps_pick``;
* the skip reads fewer items than PICK would keep ->
  ``test_a_skip_reads_every_item_that_pick_would_keep``;
* the count line of a skip says "Checked" or "Kept", or does not say "no PICK
  step" -> ``test_the_count_line_says_there_was_no_pick_step``;
* the eval card and the rates of the check drift apart ->
  ``test_the_rates_are_the_eval_card``;
* a broken estimate breaks the call -> ``test_a_broken_estimate_keeps_pick``.
* the PICK estimate counts a non-ASCII character as six (the JSON escape),
  not as one as the door does -> ``test_a_non_ascii_text_costs_as_its_length``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import structlog
from acb_auth import console_resolve
from acb_skills import narrowing, system_one
from acb_skills.narrowing import Candidate, Counts, FullItem, Narrowed

from tests.unit.test_narrowing_pick import (  # noqa: F401 — _box is an autouse fixture
    QUERY,
    Adapter,
    Door,
    SystemOne,
    _box,
    _tool,
)

CARD = Path(__file__).resolve().parents[2] / "evals" / "email_narrowing" / "fixtures" / "rate_card.json"
NO_PICK_END = (
    ", with no PICK step. A check of items this short costs more than it saves. "
    "No item was checked, so say that you read them all."
)


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> Door:
    """The decide door of ``test_narrowing_pick.py``, under the REAL client."""
    fake = Door()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


@pytest.fixture
def s1(monkeypatch: pytest.MonkeyPatch) -> SystemOne:
    fake = SystemOne()
    monkeypatch.setattr(system_one, "ask", fake.ask)
    return fake


class SizedAdapter(Adapter):
    """The test source of :class:`Adapter`, with a known READ size for each
    candidate."""

    def __init__(self, n: int, size: int | None, **kwargs: Any) -> None:
        super().__init__(n, **kwargs)
        self.size = size
        self.more = False
        self.fail_read: set[str] = set()

    async def candidates(self, query: str, filters: Mapping[str, Any]) -> Narrowed:
        got = await super().candidates(query, filters)
        return Narrowed(
            candidates=[Candidate(c.id, c.title, c.who, c.when, c.snippet, size=self.size)
                        for c in got.candidates],
            total=got.total, more=self.more,
        )

    async def read(self, ids: Sequence[str]) -> list[FullItem]:
        got = await super().read(ids)
        return [i for i in got if i.id not in self.fail_read]


def _first(out: str) -> str:
    return out.splitlines()[0]


def _read(adapter: Adapter) -> list[str]:
    return [i for call in adapter.read_ids for i in call]


# ── The rates ───────────────────────────────────────────────────────────────


def test_the_rates_are_the_eval_card() -> None:
    """ONE rate source: the check uses the eval card's prices (TODO H-278)."""
    card = json.loads(CARD.read_text(encoding="utf-8"))["tiers"]
    assert {t: (float(r["input_per_1m"]), float(r["output_per_1m"])) for t, r in card.items()} \
        == narrowing.TIER_RATES
    assert narrowing.PICK_TIER == "tier-decide"
    from evals.email_narrowing import stub_api

    assert narrowing.CHARS_PER_TOKEN == stub_api.CHARS_PER_TOKEN


# ── The decision ────────────────────────────────────────────────────────────


async def test_short_items_skip_pick_and_read_every_one(door: Door, s1: SystemOne) -> None:
    adapter = SizedAdapter(6, size=60)
    with structlog.testing.capture_logs() as caps:
        out = await _tool(adapter)(QUERY)
    assert door.bodies == [] and s1.calls == [], "PICK ran on short items"
    assert _read(adapter) == [f"m{i}" for i in range(1, 7)]
    assert _first(out) == "Read all 6 matches in full" + NO_PICK_END
    assert "dropped_of" not in out, "a skip drops nothing"
    [done] = [c for c in caps if c.get("event") == "narrowing.done"]
    assert done["pick_skipped"] is True and done["checked"] == 0 and done["kept"] == 6
    assert done["pick_cost"] * narrowing.PICK_MARGIN >= done["read_all_cost"] > 0


async def test_long_items_keep_pick(door: Door, s1: SystemOne) -> None:
    out = await _tool(SizedAdapter(6, size=5000))(QUERY)
    assert door.sizes == [6]
    assert _first(out) == "Checked 6 matches. Kept 6, dropped 0. Read 6 in full."


async def test_an_unknown_size_keeps_pick(door: Door, s1: SystemOne) -> None:
    """A source that does not set ``size`` keeps PICK, as before H-276: the
    check takes the body clip, the most that READ can give."""
    await _tool(Adapter(6))(QUERY)
    assert door.sizes == [6]
    cost = narrowing.pick_cost(QUERY, [Candidate(f"m{i}", snippet="x") for i in range(6)])
    assert cost.skip is False and cost.read_all > cost.pick * narrowing.PICK_MARGIN


async def test_more_than_the_read_cap_keeps_pick(door: Door, s1: SystemOne) -> None:
    """READ takes at most 25. A skip over 26 would cut one in silence."""
    cap = narrowing.READ_CAP
    assert cap == 25
    out = await _tool(SizedAdapter(cap, size=1))(QUERY)
    assert door.bodies == [] and _first(out) == f"Read all {cap} matches in full" + NO_PICK_END
    over = SizedAdapter(cap + 1, size=1)
    out = await _tool(over)(QUERY)
    assert door.sizes == [10, 16]
    assert _first(out).startswith(f"Checked {cap + 1} matches.")
    assert "Not read in full: 1 kept items." in out


async def test_an_overflow_keeps_pick(door: Door, s1: SystemOne) -> None:
    """More matches than candidates: the skip needs every match in hand."""
    await _tool(SizedAdapter(5, size=1, total=300))(QUERY)
    assert door.sizes == [5]
    door.bodies.clear()
    more = SizedAdapter(5, size=1)
    more.more = True
    out = await _tool(more)(QUERY)
    assert door.sizes == [5] and "of more than 5 matches" in _first(out)


def test_the_margin_is_two_and_decides() -> None:
    """The band where PICK costs less than a READ of every item, but more than
    half of it: PICK would save only if it dropped more than half. With a
    margin of 2, the tool skips there. A margin of 1 would run PICK."""
    assert narrowing.PICK_MARGIN == 2.0
    band = []
    for size in range(0, narrowing.BODY_CLIP, 20):
        cands = [Candidate(f"m{i}", title="t", who="w", when="d", snippet="s", size=size)
                 for i in range(8)]
        cost = narrowing.pick_cost(QUERY, cands)
        assert cost.skip == (cost.pick * 2 >= cost.read_all), size
        if cost.pick < cost.read_all <= 2 * cost.pick:
            band.append(size)
            assert cost.skip is True, size
        if cost.read_all > 2 * cost.pick:
            assert cost.skip is False, size
    assert band, "no size falls in the band, so the test proves nothing"


async def test_a_skip_reads_every_item_that_pick_would_keep(
    door: Door, s1: SystemOne, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recall: a skip only reads MORE. The same short items, once with the
    skip and once with PICK forced (margin 0): the skip reads a superset."""
    door.rule = lambda cid: ("no", 0.95) if cid in {"m2", "m4"} else ("yes", 0.9)
    skipped = SizedAdapter(5, size=40)
    await _tool(skipped)(QUERY)
    monkeypatch.setattr(narrowing, "PICK_MARGIN", 0.0)
    picked = SizedAdapter(5, size=40)
    await _tool(picked)(QUERY)
    assert set(_read(picked)) == {"m1", "m3", "m5"}
    assert set(_read(skipped)) == {f"m{i}" for i in range(1, 6)} >= set(_read(picked))


async def test_the_count_line_says_there_was_no_pick_step(door: Door, s1: SystemOne) -> None:
    adapter = SizedAdapter(3, size=40)
    adapter.fail_read = {"m2"}
    out = await _tool(adapter)(QUERY)
    assert _first(out) == "Read 2 of 3 matches in full" + NO_PICK_END
    assert "The read failed for 1 kept items. Read them with your other tools." in out
    line = narrowing.count_line(Counts(candidates=4, total=4, checked=0, kept=4, dropped=0,
                                       not_checked=4, read=4, pick_skipped=True))
    assert line == "Read all 4 matches in full" + NO_PICK_END
    assert "Checked" not in line and "Kept" not in line


def test_a_non_ascii_text_costs_as_its_length() -> None:
    """The decide door measures its JSON with ``ensure_ascii=False``. A
    Devanagari message must cost the same as an ASCII one of the same length,
    or PICK looks six times dearer and the tool skips it where it pays."""
    def cost(text: str) -> float:
        cands = [Candidate(f"m{i}", title="t", who="w", when="d", snippet=text, size=300)
                 for i in range(10)]
        return narrowing.pick_cost(QUERY, cands).pick

    hindi = "कल पंप का रेट " * 25
    assert len(hindi) == len("x" * len(hindi))
    assert cost(hindi) == cost("x" * len(hindi))


async def test_a_broken_estimate_keeps_pick(
    door: Door, s1: SystemOne, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no estimate")

    monkeypatch.setattr(narrowing, "pick_cost", broken)
    with structlog.testing.capture_logs() as caps:
        out = await _tool(SizedAdapter(4, size=1))(QUERY)
    assert door.sizes == [4] and _first(out).startswith("Checked 4 matches.")
    assert any(c.get("event") == "narrowing.pick_cost_failed" for c in caps)
