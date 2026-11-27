"""WS-BRIEF BR-4c — the ``numbers`` section on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §4.3. Code:
``gateway/brief/gather.py`` (``number_claims``), ``gateway/brief/run.py``,
``gateway/routes/brief_today.py``. The world is the one of
``test_brief_run_r8.py``: two orgs, a fake feed, a frozen clock, the
worker's own ``run_org``, FORCE RLS as the app role.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_numbers_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``run._gather``: drop the ``number_claims`` line.
  ``test_r8_three_waiting_threads_give_one_count_claim`` goes red.
* M2. ``gather.count_claim``: drop the ``in_app_href`` test of a card.
  ``test_r8_a_count_with_no_source_is_skipped`` goes red.
* M3. ``run._gather``: add the counts with no ``claims and lead`` guard.
  ``test_r8_a_run_with_counts_and_no_line_is_empty`` goes red.
* M4. ``store``: drop the member filter of the claims read, and
  ``brief_today``: read the run of another member. The route test goes red.
"""

# ruff: noqa: F811
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.brief import flags
from gateway.routes import brief_today

from tests.unit.test_brief_run_r8 import (  # noqa: F401
    ANA,
    BEN,
    T1,
    _claims,
    _go,
    _runs,
    _seed_and_run,
    app_engine,
    card,
    promoted,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE

pytestmark = _DB_GATE


def _three(world: Any) -> None:
    world.cards[ANA] = [card(f"email:acc:t{i}") for i in range(1, 4)]
    world.cards[BEN] = []


def _numbers(world: Any, member: str) -> list[Any]:
    run = next(r for r in _runs(world) if r.member_email == member)
    return [c for c in _claims(world, str(run.id), numbers=True) if c.section == "numbers"]


def test_r8_three_waiting_threads_give_one_count_claim(world) -> None:
    _three(world)
    _seed_and_run(world)
    got = _numbers(world, ANA)
    assert len(got) == 1
    c = got[0]
    assert c.source_kind == "count" and int(c.value) == 3 and c.value_path == "count"
    assert sorted(c.source_ids) == ["email:acc:t1", "email:acc:t2", "email:acc:t3"]
    assert c.source_id in c.source_ids and c.href.startswith("/email")
    assert str(c.organization_id) == str(world.p.org_a)
    # The lines keep their own positions, and the count follows them.
    run = next(r for r in _runs(world) if r.member_email == ANA)
    positions = [x.position for x in _claims(world, str(run.id), numbers=True)]
    assert positions == sorted(set(positions)) and c.position == max(positions)


def test_r8_the_three_counts_pass_the_checks_of_the_table(world) -> None:
    """No migration: the section CHECK holds ``numbers``, and ``source_kind``
    takes ``count``. The lead counts the brief items and no count line."""
    world.cards[ANA] = [card("email:acc:t1"), card("tasks:7", "due_today"),
                        card("tasks:8", "overdue", folded=["promise_due"])]
    world.cards[BEN] = []
    _seed_and_run(world)
    got = _numbers(world, ANA)
    assert [(c.text, int(c.value), list(c.source_ids), c.source_kind) for c in got] == [
        ("1 thread waits for your reply", 1, ["email:acc:t1"], "count"),
        ("1 task is due today", 1, ["tasks:7"], "count"),
        ("1 promise you made falls due", 1, ["tasks:8"], "count")]
    run = next(r for r in _runs(world) if r.member_email == ANA)
    assert run.lead.endswith("3 things need you.")


def test_r8_a_count_with_no_source_is_skipped(world) -> None:
    world.cards[ANA] = [card("email:acc:t1", href="//evil.example"),
                        card("nobody:1"), card("tasks:42", "overdue")]
    world.cards[BEN] = []
    _seed_and_run(world)
    assert _numbers(world, ANA) == []
    run = next(r for r in _runs(world) if r.member_email == ANA)
    assert run.state == "ready"  # the line of the overdue task still stands


def test_r8_a_run_with_counts_and_no_line_is_empty(world) -> None:
    """The 7 cards that the brief shows can make no claim, and a card after
    them can be counted. That run is ``empty``, as before BR-4c."""
    world.cards[ANA] = [card(f"nobody:{i}", "overdue", score=90) for i in range(7)]
    world.cards[ANA].append(card("email:acc:t1", score=1))
    world.cards[BEN] = []
    _seed_and_run(world)
    run = next(r for r in _runs(world) if r.member_email == ANA)
    assert run.state == "empty" and run.lead is None
    assert _claims(world, str(run.id), numbers=True) == []


def _get(world: Any, email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:chat"], roles=["member"]))

    async def go() -> Any:
        token = bind_tenant(org)
        try:
            return await brief_today.get_today(kind="brief", user=user)
        except HTTPException as exc:
            return exc.status_code
        finally:
            release_tenant(token)

    return _go(world, go)


def test_r8_get_today_serves_the_count_to_its_member_only(world, monkeypatch) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        brief_enabled=True, brief_orgs=world.p.org_a, brief_in_app=True))
    monkeypatch.setattr(brief_today, "_now", lambda: T1 + timedelta(minutes=30))
    _three(world)
    world.cards[BEN] = [card("tasks:42", "overdue")]  # Ben has a run of his own
    _seed_and_run(world)
    body = _get(world, ANA, world.p.org_a)
    counts = [c for c in body["claims"] if c["section"] == "numbers"]
    assert len(counts) == 1 and counts[0]["source_kind"] == "count"
    assert counts[0]["text"] == "3 threads wait for your reply"
    assert counts[0]["href"].startswith("/email")
    # Ben reads his own run, with no count of Ana. The same address in org B
    # has no run.
    other = _get(world, BEN, world.p.org_a)
    assert [c["section"] for c in other["claims"]] == ["needs_you"]
    assert _get(world, ANA, world.p.org_b) == 404


@pytest.mark.parametrize("enabled,holds_org", [(False, True), (True, False)])
def test_r8_flags_off_give_no_run_and_no_claim(world, monkeypatch, enabled, holds_org) -> None:
    """``BRIEF_ENABLED`` off, or an org that ``BRIEF_ORGS`` does not hold: no
    run, so no claim and no read of the feed."""
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        brief_enabled=enabled, brief_orgs=world.p.org_a if holds_org else world.p.org_b))
    _three(world)
    _seed_and_run(world)
    assert _runs(world) == [] and world.reads["snapshot"] == 0
