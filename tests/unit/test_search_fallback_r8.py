"""Task search on most of the words, on a REAL Postgres, as the app role (R8).

HRD-4 (task 2983), ``find_engine.md`` §9.2. ``/projects/search`` needs every
word. When no task has all of them and the query holds two words or more, it
ranks the tasks that hold SOME of the words by how many each holds.

What only a database can prove, and so lives here and not in
``test_search_fallback.py``:

* **the count.** ``held`` is a sum of ``CASE`` arms over the real columns, and
  the order is evaluated before the ``LIMIT``.
* **the same wall.** The fallback runs inside the same visibility, triage and
  tenant rules. A private task of another member and a task of another org are
  never a fallback row.
* **no change for an all-words hit.** The answer carries no ``partial`` key.

Runs on the H3 rehearsal's ``promoted`` fixture, as the description suite does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (
    _DB_GATE,
    promoted,  # noqa: F401 - a fixture, used by name
)
from tests.unit.test_projects_search_description_r8 import _ids, _project, _search, _task

pytestmark = _DB_GATE

ANA = "ana@fallback.test"
BOB = "bob@fallback.test"
CARA = "cara@fallback-b.test"

#: Words no other seeded row holds.
W1, W2, W3 = "quillfrost", "marblepeak", "tinkerwisp"


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    a, b = promoted.org_a, promoted.org_b
    ids: dict[str, str] = {}
    with promoted.admin_engine.begin() as c:
        for email, org in ((ANA, a), (BOB, a), (CARA, b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        team, lane = _project(c, org=a, name="Ops")
        mine, plane = _project(c, org=a, name="Bob's area", owner=BOB, grant=BOB)
        other, olane = _project(c, org=b, name="B Ops")
        # Two of the three words, in the title: the best fallback row, and the oldest.
        ids["two"] = _task(c, org=a, project=team, lane=lane, number=11,
                           title=f"{W1.title()} {W2} plan", updated="2026-01-01")
        # One word, in the description, and newer than the rest.
        ids["one_new"] = _task(c, org=a, project=team, lane=lane, number=12,
                               title="Plain chores", description=f"Call about {W1}.",
                               updated="2026-10-09")
        # One word, in the title, older.
        ids["one_old"] = _task(c, org=a, project=team, lane=lane, number=13,
                               title=f"{W3} list", updated="2026-05-01")
        # All three words are never together in one task.
        # Private to Bob, with two words. Ana must not see it.
        ids["private"] = _task(c, org=a, project=mine, lane=plane, number=1,
                               title=f"{W2} {W3} secret")
        # Org B, with two words.
        ids["other_org"] = _task(c, org=b, project=other, lane=olane, number=1,
                                 title=f"{W1} {W3} for B")
    yield promoted, ids
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})


async def test_r8_a_miss_ranks_by_the_words_each_task_holds(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{W1} {W2} {W3}")
    assert out["partial"] is True and out["words"] == 3
    # Two words first, although it is the oldest. Then the newer of the singles.
    assert _ids(out) == [ids["two"], ids["one_new"], ids["one_old"]]
    assert [r["matched"] for r in out["rows"]] == [2, 1, 1]
    assert out["total"] == 3 and out["truncated"] is False


async def test_r8_the_fallback_keeps_the_wall(seeded, monkeypatch):
    ns, ids = seeded
    ana = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{W1} {W2} {W3}")
    assert ids["private"] not in _ids(ana) and ids["other_org"] not in _ids(ana)
    # Positive controls: the absence is the wall, not a broken seed.
    bob = await _search(ns, monkeypatch, who=BOB, org=ns.org_a, q=f"{W2} {W3} {W1}")
    assert ids["private"] in _ids(bob)
    cara = await _search(ns, monkeypatch, who=CARA, org=ns.org_b, q=f"{W1} {W2} {W3}")
    assert _ids(cara) == [ids["other_org"]]


async def test_r8_an_all_words_hit_has_no_fallback_keys(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{W1} {W2}")
    assert _ids(out) == [ids["two"]]
    assert "partial" not in out and "words" not in out
    assert all("matched" not in r for r in out["rows"])


async def test_r8_a_description_word_gives_a_snippet(seeded, monkeypatch):
    ns, ids = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q=f"{W1} {W2} {W3}")
    row = next(r for r in out["rows"] if r["id"] == ids["one_new"])
    assert W1 in (row["snippet"] or "")


async def test_r8_no_word_at_all_is_a_plain_zero(seeded, monkeypatch):
    ns, _ids_unused = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a,
                        q="zzqxnothing yyqxnothing")
    assert out["rows"] == [] and "partial" not in out


async def test_r8_a_number_query_is_not_a_fallback(seeded, monkeypatch):
    ns, _ids_unused = seeded
    out = await _search(ns, monkeypatch, who=ANA, org=ns.org_a, q="#999999")
    assert out["rows"] == [] and "partial" not in out
