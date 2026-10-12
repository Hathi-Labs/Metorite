"""R8 — CRM-X2, the CRM provider of the command bar (WS-53,
``crm_platform.md`` §19.6 and §19.10).

Done-when items 1, 4, 5 and 7, against a REAL Postgres, through the WHOLE
shell route (``shell_search``), as the non-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS):

1. A member with the CRM types part of a deal name. The answer holds a CRM
   group with that deal, and its link opens the record sheet.
4. A member that the access rule refuses gets no CRM group. The rule reads
   ``crm_settings`` in the tenant session, so only a database proves it.
5. A member of organization B never gets a record of organization A. The
   word matches in both, and only row level security keeps A's rows out.
7. A converted lead does not show, as on the Leads list.

It also proves the §19.10 notes on a real database: the four reads merge by
``last_activity_at``, newest first, and no read asks for the next activity.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database. ⚠️ It SKIPS without
``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.shell import search as shell
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# The helpers and the fixture of the CRM-X1 suite, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_crm_find_and_page_r8 import (  # noqa: F401
    _FIRST_STAGE,
    _admin,
    _make,
    _native,
    _principal,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE


async def _search(p: Any, user: Any, q: str, scope: str | None = None) -> dict:
    """The shell route, as the request would run it: the tenant comes from the user."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            bind_tenant(user.organization_id)
            return await shell.shell_search(q=q, scope=scope, user=user)
    finally:
        release_tenant(token)


def _crm(answer: dict) -> list[dict]:
    group = next((g for g in answer["groups"] if g["app"] == "crm"), None)
    return group["items"] if group else []


@pytest.fixture
def roomy(monkeypatch):
    """A cold scratch database is slower than the bar's budget, and a provider
    cut off by the deadline also returns nothing. Room here, and the log check
    in each case proves the provider finished."""
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 60.0)


def _no_provider_trouble(caplog: Any) -> None:
    assert not [r for r in caplog.records if "provider" in r.getMessage()], (
        "the CRM provider failed or timed out, so an empty answer proves nothing"
    )


# ── 1 and 5: a deal by part of its name, and nothing of another tenant ─────

async def test_a_deal_comes_back_with_its_sheet_link_and_nothing_of_org_a_reaches_b(
    ns, app_engine, roomy, caplog, monkeypatch,  # noqa: F811
) -> None:
    from gateway.routes.crm import core

    async def no_next_activity(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the shell search must not read the next activity (§19.10)")

    monkeypatch.setattr(core, "attach_next_activity", no_next_activity)
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    word = f"falcon{tag}"
    zephyr = await _make(ns, a, "/crm/organizations", {"name": f"Zephyr {tag}"})
    deal = await _make(ns, a, "/crm/deals", {"name": f"Big {word} order", "company_id": zephyr})
    await _make(ns, b, "/crm/deals", {"name": f"Bee {word} order"})

    with caplog.at_level(logging.INFO, logger=shell.__name__):
        answer_a = await _search(ns, _admin(a), word[2:9])
        answer_b = await _search(ns, _admin(b), word)
    _no_provider_trouble(caplog)

    assert _crm(answer_a) == [{
        "kind": "deal", "title": f"Big {word} order", "hint": f"Deal · Zephyr {tag}",
        "href": f"/crm?tab=deals&deal={deal}",
    }]
    # B finds its own deal, so the search runs there, and never A's.
    assert [i["title"] for i in _crm(answer_b)] == [f"Bee {word} order"]


# ── the merge: newest activity first, across the four entities ─────────────

async def test_the_four_reads_merge_newest_activity_first(
    ns, app_engine, roomy, caplog,  # noqa: F811
) -> None:
    tag = uuid.uuid4().hex[:6]
    a = ns.org_a
    word = f"merge{tag}"
    await _native(ns, a)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_companies (organization_id, name, last_activity_at) "
            "VALUES (CAST(:o AS uuid), :n, now() - interval '3 days')"),
            {"o": a, "n": f"Old {word} Co"})
        c.execute(text(
            "INSERT INTO crm_contacts (organization_id, first_name, last_activity_at) "
            "VALUES (CAST(:o AS uuid), :n, now() - interval '1 hour')"),
            {"o": a, "n": f"New{word}"})
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, last_activity_at) "
            f"VALUES (CAST(:o AS uuid), :n, {_FIRST_STAGE}, now() - interval '1 day')"),
            {"o": a, "n": f"Mid {word} deal"})
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
            "VALUES (CAST(:o AS uuid), :n, (SELECT id FROM crm_lead_statuses "
            "WHERE organization_id = CAST(:o AS uuid) ORDER BY position LIMIT 1))"),
            {"o": a, "n": f"Quiet {word} lead"})

    with caplog.at_level(logging.INFO, logger=shell.__name__):
        answer = await _search(ns, _admin(a), word)
    _no_provider_trouble(caplog)
    # A record with no activity yet comes last.
    assert [(i["kind"], i["title"]) for i in _crm(answer)] == [
        ("contact", f"New{word}"),
        ("deal", f"Mid {word} deal"),
        ("company", f"Old {word} Co"),
        ("lead", f"Quiet {word} lead"),
    ]


async def test_each_read_takes_its_most_active_rows_not_its_newest(
    ns, app_engine, roomy, caplog,  # noqa: F811
) -> None:
    # §19.10: the default sort of leads is `created_at`. Six leads match, and
    # the OLDEST one has the newest activity. A read by `created_at` takes
    # the five newest and leaves it out before the merge can see it.
    tag = uuid.uuid4().hex[:6]
    a = ns.org_a
    word = f"busy{tag}"
    await _native(ns, a)
    with ns.admin_engine.begin() as c:
        for n in range(6):
            c.execute(text(
                "INSERT INTO crm_leads (organization_id, lead_name, status_id, created_at, "
                "last_activity_at) VALUES (CAST(:o AS uuid), :name, (SELECT id FROM "
                "crm_lead_statuses WHERE organization_id = CAST(:o AS uuid) ORDER BY "
                "position LIMIT 1), now() - make_interval(days => :age), "
                "now() - make_interval(days => :idle))"),
                {"o": a, "name": f"Lead {n} {word}", "age": 10 - n,
                 "idle": 1 if n == 0 else 5 + n})

    with caplog.at_level(logging.INFO, logger=shell.__name__):
        answer = await _search(ns, _admin(a), word)
    _no_provider_trouble(caplog)
    titles = [i["title"] for i in _crm(answer)]
    assert len(titles) == shell.PER_APP
    assert titles[0] == f"Lead 0 {word}"


# ── 7: a converted lead stays out ──────────────────────────────────────────

async def test_a_converted_lead_does_not_show(ns, app_engine, roomy, caplog) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a = ns.org_a
    word = f"conv{tag}"
    await _native(ns, a)
    open_lead = await _make(ns, a, "/crm/leads", {"lead_name": f"Open {word}"})
    deal = await _make(ns, a, "/crm/deals", {"name": f"Became {tag}"})
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id, "
            "converted_deal_id, converted_at) VALUES (CAST(:o AS uuid), :n, "
            "(SELECT id FROM crm_lead_statuses WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY position LIMIT 1), CAST(:d AS uuid), now())"),
            {"o": a, "n": f"Converted {word}", "d": deal})

    with caplog.at_level(logging.INFO, logger=shell.__name__):
        answer = await _search(ns, _admin(a), word, scope="/crm")
    _no_provider_trouble(caplog)
    assert _crm(answer) == [{
        "kind": "lead", "title": f"Open {word}", "hint": "Lead",
        "href": f"/crm?tab=leads&lead={open_lead}",
    }]
    assert answer["groups"][0]["app"] == "crm"


# ── 4: the access rule, read in the tenant session ──────────────────────────

async def test_a_member_the_access_rule_refuses_gets_no_crm_group(
    ns, app_engine, roomy,  # noqa: F811
) -> None:
    tag = uuid.uuid4().hex[:6]
    a = ns.org_a
    word = f"rule{tag}"
    await _native(ns, a)
    await _make(ns, a, "/crm/deals", {"name": f"Guarded {word}"})
    member = _principal(f"m.{tag}@crm-x2.test", a, "feature:crm")

    # With no `everyone` row, the rule admits admins only (CRM-T5).
    assert _crm(await _search(ns, member, word)) == []
    # The admin opens the CRM to everyone, and the same member finds the deal.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET access = 'everyone' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": a})
    assert [i["title"] for i in _crm(await _search(ns, member, word))] == [f"Guarded {word}"]
