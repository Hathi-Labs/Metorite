"""C5a meets KR-ZERO: a dark ``studio`` source changes no answer (WS-STU C5a, WS-61 KR-ZERO).

``studio`` joined ``sources.REGISTRY`` with C5a, and KR-ZERO (task 2974) asks
each source of it again after a zero answer. The two shipped on the same day and
the KR-ZERO tests assumed a registry of seven. This file fences the promise that
matters to a member: while ``STUDIO_SEARCH_ORGS`` holds no org, a member who
holds ``feature:studio`` gets the SAME answer as a member who does not, with
``CONTEXT_RECENCY`` off (the KR-1 answer, byte for byte) and on (the zero
answer with its second pass). The studio source is real here, with its own
switches closed. Only the seven apps of the world are faked.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from gateway.retrieval import company, sources

from tests.unit.test_company_search_recency import recency  # noqa: F401
from tests.unit.test_company_search_sources import ALL, member
from tests.unit.test_company_search_zero import World


@pytest.fixture()
def world(monkeypatch) -> World:
    """The seven fake apps of KR-ZERO's world. The ``studio`` source keeps its
    real reader and its real switch: that is what this file fences."""
    w = World()
    for key, source in list(sources.REGISTRY.items()):
        if key in w.corpus:
            monkeypatch.setitem(sources.REGISTRY, key,
                                dataclasses.replace(source, read=w.reader(key), gate=None))
    return w


def _ask(q: str, *features: str) -> dict:
    return asyncio.run(company.company_search(
        q=q, apps=None, k=5, user=member(*features)))


def test_the_registry_holds_studio_and_the_world_does_not_fake_it(world):
    assert "studio" in sources.REGISTRY
    assert "studio" not in world.corpus
    assert sources.REGISTRY["studio"].gate is not None, "the real switch stays"


@pytest.mark.parametrize("flag", [False, True])
def test_a_dark_studio_source_changes_no_zero_answer(world, recency, flag):  # noqa: F811
    recency(flag)
    plain = _ask("Aroh Attero", *ALL)
    with_studio = _ask("Aroh Attero", *ALL, "studio")
    assert with_studio == plain


@pytest.mark.parametrize("flag", [False, True])
def test_a_dark_studio_source_changes_no_answer_with_hits(world, recency, flag):  # noqa: F811
    recency(flag)
    plain = _ask("Aroh", *ALL)
    with_studio = _ask("Aroh", *ALL, "studio")
    assert plain["hits"], "the corpus holds a hit for this word"
    assert with_studio == plain


def test_flag_off_the_zero_answer_is_the_kr1_answer_for_a_member_with_studio(world, recency):  # noqa: F811
    recency(False)
    assert _ask("Aroh Attero", *ALL, "studio") == {"query": "Aroh Attero", "hits": [], "skipped": []}


@pytest.mark.parametrize("flag", [False, True])
def test_a_dark_studio_source_is_never_named_skipped_when_the_time_runs_out(
        world, recency, monkeypatch, flag):  # noqa: F811
    """KR-ZERO review of C5a. Studio is last in the registry, so it is the first
    key that ``_ask_each`` skips when the budget is spent. A source that was
    never open must not read "ran out of time" for one member and not for the
    other."""
    recency(flag)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 0.3)
    world.slow = {"Aroh": 0.6}
    plain = _ask("Aroh", *ALL)
    with_studio = _ask("Aroh", *ALL, "studio")
    assert plain["skipped"], "the budget ran out: the world has skipped sources"
    assert "studio" not in with_studio["skipped"]
    assert with_studio["skipped"] == plain["skipped"]


@pytest.mark.parametrize("flag", [False, True])
def test_naming_studio_in_apps_changes_nothing_while_it_is_dark(world, recency, flag):  # noqa: F811
    """``apps=studio`` is accepted (no 422) and answers like ``apps=None``."""
    recency(flag)
    asked = asyncio.run(company.company_search(
        q="Aroh", apps="studio", k=5, user=member(*ALL, "studio")))
    base = asyncio.run(company.company_search(
        q="Aroh", apps=None, k=5, user=member(*ALL, "studio")))
    assert asked["hits"] == base["hits"]
    assert asked["skipped"] == base["skipped"]
