"""R8 — the mapping pipeline and the kinds of rule, on a real Postgres
(WS-53 CRM-Q20-1, ``crm_platform.md`` §18.9.6).

Each step runs as the NON-privileged role of the phase-4 rehearsal, so RLS
binds each read and each write. A fake agrees with any SQL, so only this
suite proves the reads of the pipeline, the draft, the 422 of rule 1 and
the cursor row. The ``decide`` facade is a scripted fake: no real model
call.

* Item 8: ``mapping_plans.commit`` gives a 422 for a ``target`` or a
  ``stage`` with no ``role`` of the same slot and module: on a draft
  approve and on a restore. A legal set approves.
* Item 3: ``write_draft`` writes one draft and no rule row, and a second
  call with the same items writes nothing.
* Items 1, 7, 9 and 10, end to end: the run reads the profile, the module
  records and the field definitions of its own org, asks through the fake,
  writes a draft with the three kinds, and keeps the schema hash in the
  cursor row ``zoho:map_pipeline``. A second run on the same schema makes 0
  ``ask`` calls. An org outside ``CRM_DECIDE_ORGS`` makes 0. A run in
  ``shadow`` writes the hash and no draft. The admin can approve the draft,
  and it changes no live rule.

``tests/unit/test_crm_field_rules_r8.py`` holds the fixture and the fake
Zoho. The payer rule's own SQL (``my_day_loop.departed``) is proven by
``test_crm_note_labels_r8.py``, so this suite answers it with a fake.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _req,
    fr,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _q,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

PLANS = "/crm/mapping/plans"

ROLE = {"kind": "role", "slot": "deal", "module": "Commercials", "profile_version": 1}
TARGET = {"kind": "target", "slot": "deal", "module": "Commercials", "target": "next_step",
          "fields": ["Next"], "transform": "copy"}
STAGE = {"kind": "stage", "slot": "deal", "module": "Commercials",
         "field_api_name": "Status", "values": {"Won": "won", "Lost": "lost"}}

#: The answers of the fake decide, in the shape of the eval's set.
ANSWERS = {
    "role.deal": {"pick": "Commercials", "p": 0.86},
    "t.expected_close_date": {"pick": "Closing Date", "p": 0.9},
    "s:Open": {"pick": "open", "p": 0.9},
    "s:On Hold": {"pick": "open", "p": 0.88},
}

#: A label of org B that org A's run must never send.
SECRET_B = "SECRET-B-Tender-Register"


class FakeDecide:
    """``acb_llm.decide``, scripted by the eval's asker. Records each call."""

    def __init__(self, answers: dict[str, Any]) -> None:
        from evals.crm_mapping.run import ScriptedAsker

        self.scripted = ScriptedAsker(answers)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        return await self.scripted("x", [(state, questions)])


@pytest.fixture
def mp_env(fr, monkeypatch):
    """``fr``, with the modes on for org A only, a fake decide, a fake Redis
    and the payer answered, cleaned after."""
    import acb_llm
    from acb_common import get_settings, tenant_redis
    from acb_common.tenant_redis import TenantRedis
    from gateway import decide_features as df
    from gateway.crm_sync import note_labels

    from tests.unit.test_feed_decide import FakeRedis

    p, a, b = fr
    settings = get_settings()
    monkeypatch.setattr(settings, "decide_feature_modes",
                        "crm.map_role=on,crm.map_target=on,crm.map_stage=on", raising=False)
    monkeypatch.setattr(settings, "crm_decide_orgs", p.org_a, raising=False)
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()
    df._cooldown_until.clear()
    fake = FakeDecide(ANSWERS)
    monkeypatch.setattr(acb_llm, "decide", fake)
    redis = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(redis))

    async def _left(org, member):
        return None

    monkeypatch.setattr(note_labels, "_member_left", _left)
    p.fake = fake
    yield p, a, b
    df._cooldown_until.clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


# ── Seeds, as the superuser ──────────────────────────────────────────────────


def _module_row(api_name: str, label: str, display: str) -> dict[str, Any]:
    return {"api_name": api_name, "tab": api_name, "label": label, "singular_label": label,
            "custom": True, "generated_type": "custom", "api_supported": True,
            "visible": True, "mirrored": True, "generic": True,
            "scope": "ZohoCRM.modules.custom.READ", "display_field": display,
            "fields_pending": False}


def _seed_module(p: Any, org: str, api_name: str, label: str, *, statuses: bool = True,
                 records: int = 24) -> None:
    """One generic module of *org*: its profile row, its field definitions
    and its records."""
    fields = [
        ("Commercial_Name", "Commercial Name", "text"),
        ("Closing_Date", "Closing Date", "date"),
        ("Probability", "Probability", "percent"),
        ("Next", "Next Step Text", "text"),
        ("Status", "Status", "select"),
    ]
    states = ["Won", "Lost", "Open", "On Hold"]
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = jsonb_set(profile, '{modules}', "
            "COALESCE(profile -> 'modules', '[]'::jsonb) || CAST(:m AS jsonb)) "
            "WHERE organization_id = CAST(:o AS uuid) AND version = (SELECT max(version) "
            "FROM crm_source_profiles WHERE organization_id = CAST(:o AS uuid))"),
            {"o": org, "m": json.dumps([_module_row(api_name, label, "Commercial_Name")])})
        for position, (key, field_label, kind) in enumerate(fields):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
                "source, provider_api_name, position) VALUES (CAST(:o AS uuid), :e, :k, "
                ":l, :t, 'zoho', :k, :n) ON CONFLICT DO NOTHING"),
                {"o": org, "e": f"module:{api_name}", "k": key, "l": field_label,
                 "t": kind, "n": position})
        for i in range(records):
            values = {"Commercial_Name": f"{label} {i}", "Closing_Date": f"2026-11-{1 + i:02d}",
                      "Probability": (i * 9) % 100}
            if statuses:
                values["Status"] = states[i % len(states)]
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, "
                "name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :x, :n, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": api_name, "x": f"{api_name}-{i}",
                 "n": values["Commercial_Name"], "f": json.dumps(values)})


def _no_deals(p: Any, org: str) -> None:
    """Empty the deal slot of *org*: no mirrored deal."""
    with p.admin_engine.begin() as c:
        for table in ("crm_deal_contacts", "crm_deals"):
            c.execute(text(f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                      {"o": org})


def _versions(p: Any, org: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _q(
        p, "SELECT version, state, created_by, based_on, rules FROM crm_mapping_plans "
           "WHERE organization_id = CAST(:o AS uuid) ORDER BY version", o=org)]


def _rule_rows(p: Any, org: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _q(
        p, "SELECT id, status, target_column FROM crm_field_rules "
           "WHERE organization_id = CAST(:o AS uuid) ORDER BY id", o=org)]


def _cursor(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = 'zoho:map_pipeline'", o=org)
    return rows[0][0] if rows else None


async def _plans(org: str) -> dict[str, Any]:
    got = await _req(_admin_of(org), "GET", PLANS)
    assert got.status_code == 200, got.text
    return got.json()


def _insert_version(p: Any, org: str, state: str, items: list[dict[str, Any]],
                    based_on: int | None) -> int:
    with p.admin_engine.begin() as c:
        version = c.execute(text(
            "SELECT COALESCE(max(version), 0) + 1 FROM crm_mapping_plans "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, rules, "
            "created_by, based_on) VALUES (CAST(:o AS uuid), :v, :s, CAST(:r AS jsonb), "
            "'seed', :b)"), {"o": org, "v": version, "s": state, "r": json.dumps(items),
                             "b": based_on})
    return int(version)


async def _write_draft(org: str, items: list[dict[str, Any]]) -> Any:
    from acb_common.db import tenant_session
    from gateway.routes.crm import mapping_plans

    async with tenant_session(org) as db:
        return await mapping_plans.write_draft(db, items=items)


async def _run(org: str) -> Any:
    from gateway.crm_sync import map_pipeline

    return await map_pipeline.map_after_run(org)


# ── Item 8: rule 1 in commit ─────────────────────────────────────────────────


async def test_8_an_approve_of_a_target_with_no_role_is_422(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    bad = _insert_version(p, p.org_a, "draft", [TARGET], approved)
    count = len(_versions(p, p.org_a))
    got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{bad}/approve")
    assert got.status_code == 422, got.text
    assert got.json()["detail"]["code"] == "role_missing"
    assert len(_versions(p, p.org_a)) == count, "the refused approve wrote nothing"
    assert (await _plans(p.org_a))["approved"] == approved

    good = _insert_version(p, p.org_a, "draft", [ROLE, TARGET, STAGE], approved)
    got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{good}/approve")
    assert got.status_code == 200, got.text
    kinds = sorted(r["kind"] for r in got.json()["rules"])
    assert kinds == ["role", "stage", "target"]


async def test_8_a_restore_of_a_target_with_no_role_is_422(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    old = _insert_version(p, p.org_a, "superseded", [STAGE], approved)
    count = len(_versions(p, p.org_a))
    rules = _rule_rows(p, p.org_a)
    got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{old}/restore")
    assert got.status_code == 422, got.text
    assert got.json()["detail"]["code"] == "role_missing"
    assert len(_versions(p, p.org_a)) == count
    assert _rule_rows(p, p.org_a) == rules

    whole = _insert_version(p, p.org_a, "superseded", [ROLE, STAGE], approved)
    got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{whole}/restore")
    assert got.status_code == 201, got.text
    assert _rule_rows(p, p.org_a) == rules, "a kind changes no live rule"


async def test_8_rules_2_and_3_refuse_on_approve(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    for items, code in (
        ([ROLE, {**TARGET, "target": "amount"}], "bad_target"),
        ([ROLE, {**STAGE, "values": {"Won": "victory"}}], "bad_stage"),
    ):
        bad = _insert_version(p, p.org_a, "draft", items, approved)
        got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{bad}/approve")
        assert got.status_code == 422, got.text
        assert got.json()["detail"]["code"] == code


# ── Item 3: one draft, no rule row ───────────────────────────────────────────


async def test_3_write_draft_writes_one_draft_and_no_rule_row(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    rules = _rule_rows(p, p.org_a)
    row = await _write_draft(p.org_a, [ROLE, TARGET])
    assert row is not None
    version, state, by, based_on, items = _versions(p, p.org_a)[-1]
    assert (version, state, by, based_on) == (row.version, "draft", "suggestion", approved)
    assert sorted(i["kind"] for i in items) == ["role", "target"]
    assert _rule_rows(p, p.org_a) == rules, "a draft writes no rule row"
    assert await _write_draft(p.org_a, [ROLE, TARGET]) is None, "the same items: nothing"
    assert len(_versions(p, p.org_a)) == version


# ── Items 1, 3, 7, 9 and 10, end to end ──────────────────────────────────────


async def test_the_run_writes_a_draft_with_the_three_kinds(mp_env) -> None:
    p, a, b = mp_env
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    _no_deals(p, p.org_a)
    _no_deals(p, p.org_b)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    _seed_module(p, p.org_b, "Tender_Register", SECRET_B)
    rules = _rule_rows(p, p.org_a)

    got = await _run(p.org_a)
    assert got.reason == "done", got
    assert got.draft is not None and got.requests == len(p.fake.calls) >= 1
    for call in p.fake.calls:
        assert call["module_slug"] == "crm" and call["member_proven"] is True
        assert SECRET_B not in json.dumps(call["state"]), "org B's schema never leaves"
    _version, state, by, _based, items = _versions(p, p.org_a)[-1]
    assert (state, by) == ("draft", "suggestion")
    by_kind = {k: [i for i in items if i["kind"] == k] for k in ("role", "target", "stage")}
    assert [(i["slot"], i["module"]) for i in by_kind["role"]] == [("deal", "Commercials")]
    targets = {i["target"]: i["fields"] for i in by_kind["target"]}
    assert targets == {"name": ["Commercial_Name"], "expected_close_date": ["Closing_Date"],
                       "probability": ["Probability"]}
    (stage,) = by_kind["stage"]
    assert stage["field_api_name"] == "Status"
    assert stage["values"] == {"Won": "won", "Lost": "lost", "Open": "open", "On Hold": "open"}
    assert stage["unsorted"] == 0
    assert _rule_rows(p, p.org_a) == rules, "never a live rule"

    cursor = _cursor(p, p.org_a)
    assert set(cursor["schema"]) == {"slots", "module:Commercials"}
    assert _cursor(p, p.org_b) is None, "org B ran nothing"

    # Item 10: a second run on the same schema makes 0 ask calls.
    calls = len(p.fake.calls)
    count = len(_versions(p, p.org_a))
    again = await _run(p.org_a)
    assert again.reason == "done" and again.requests == 0
    assert len(p.fake.calls) == calls
    assert len(_versions(p, p.org_a)) == count, "the same items: no new draft"

    # The admin approves the draft, and it changes no live rule.
    draft = _versions(p, p.org_a)[-1][0]
    approve = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{draft}/approve")
    assert approve.status_code == 200, approve.text
    assert _rule_rows(p, p.org_a) == rules
    third = await _run(p.org_a)
    assert third.requests == 0 and len(_versions(p, p.org_a)) == count


async def test_9_the_unsorted_count_reads_the_records(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    _no_deals(p, p.org_a)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    p.fake.scripted.answers["s:On Hold"] = {"pick": "open", "p": 0.5, "p2": 0.5}
    got = await _run(p.org_a)
    assert got.reason == "done"
    items = _versions(p, p.org_a)[-1][4]
    (stage,) = [i for i in items if i["kind"] == "stage"]
    assert "On Hold" not in stage["values"] and stage["unsorted"] == 1


async def test_1_an_org_outside_the_list_makes_no_ask_call(mp_env) -> None:
    p, _a, b = mp_env
    await _imported(p, p.org_b, b)
    _no_deals(p, p.org_b)
    _seed_module(p, p.org_b, "Commercials", "Commercials")
    got = await _run(p.org_b)
    assert got.reason == "off"
    assert p.fake.calls == []
    assert _cursor(p, p.org_b) is None
    assert [v for v in _versions(p, p.org_b) if v[2] == "suggestion"] == []


async def test_10_a_shadow_run_writes_the_hash_and_no_draft(mp_env, monkeypatch) -> None:
    from acb_common import get_settings
    from gateway import decide_features as df

    p, a, _b = mp_env
    monkeypatch.setattr(get_settings(), "decide_feature_modes",
                        "crm.map_role=shadow,crm.map_target=shadow,crm.map_stage=shadow",
                        raising=False)
    df._parse_modes.cache_clear()
    await _imported(p, p.org_a, a)
    _no_deals(p, p.org_a)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    got = await _run(p.org_a)
    assert got.reason == "done" and got.draft is None
    assert [v for v in _versions(p, p.org_a) if v[2] == "suggestion"] == []
    cursor = _cursor(p, p.org_a)
    assert "slots" in cursor["schema"]
    asked = len(p.fake.calls)
    again = await _run(p.org_a)
    assert again.requests == 0 and len(p.fake.calls) == asked


async def test_the_cursor_row_and_the_draft_are_the_orgs_own(mp_env) -> None:
    """The cursor row and the draft live under FORCE RLS: the app role bound
    to org B reads no row of org A, and bound to org A it reads each one."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    _no_deals(p, p.org_a)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    assert (await _run(p.org_a)).reason == "done"

    def counts_as(org: str) -> tuple[int, int]:
        fresh = create_engine(p.app_url, poolclass=NullPool)
        try:
            with fresh.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                rows = c.execute(text("SELECT count(*) FROM crm_sync_cursors "
                                      "WHERE module = 'zoho:map_pipeline'")).scalar()
                plans = c.execute(text("SELECT count(*) FROM crm_mapping_plans "
                                       "WHERE created_by = 'suggestion'")).scalar()
                return int(rows), int(plans)
        finally:
            fresh.dispose()

    assert counts_as(p.org_a) == (1, 1)
    assert counts_as(p.org_b) == (0, 0)


# ── Batch M review P1: one open draft holds both kinds of change ─────────────


def _put(field: str, column: str) -> dict[str, Any]:
    return {"op": "put", "entity": "leads", "field_api_name": field, "target_column": column}


def _field_items(items: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(i["field_api_name"], i["target_column"])
            for i in items if i.get("kind", "field") == "field"}


def _kind_items(items: list[dict[str, Any]]) -> list[str]:
    return sorted(i["kind"] for i in items if i.get("kind", "field") != "field")


async def test_a_suggestion_keeps_the_picks_by_hand_of_the_open_draft(mp_env) -> None:
    """A pick by hand (draft H), then the pipeline writes its draft S. S
    builds on H, so the pick stays in the draft that the card shows."""
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    hand = await _req(_admin_of(p.org_a), "POST", PLANS,
                      {"changes": [_put("Why_Lost", "lost_note")]})
    assert hand.status_code == 201, hand.text
    row = await _write_draft(p.org_a, [ROLE, TARGET])
    assert row is not None
    _version, state, by, based_on, items = _versions(p, p.org_a)[-1]
    # Round 2 review: the draft holds the admin's pick, so the admin stays its
    # author, and the history never says "Suggested by Metorite" for it.
    assert (state, by, based_on) == ("draft", "admin@crm-sync.test", approved)
    assert ("Why_Lost", "lost_note") in _field_items(items), "the pick stays"
    assert _kind_items(items) == ["role", "target"]
    assert await _write_draft(p.org_a, [ROLE, TARGET]) is None, "the open draft holds them"


async def test_a_pick_by_hand_joins_the_suggestion(mp_env) -> None:
    """The pipeline writes S, then the admin picks a field by hand. The new
    draft keeps the items of S, so an approve applies what the card shows."""
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    assert await _write_draft(p.org_a, [ROLE, TARGET, STAGE]) is not None
    hand = await _req(_admin_of(p.org_a), "POST", PLANS,
                      {"changes": [_put("Why_Lost", "lost_note")]})
    assert hand.status_code == 201, hand.text
    body = hand.json()
    assert body["based_on"] == approved
    assert ("Why_Lost", "lost_note") in _field_items(body["rules"])
    assert _kind_items(body["rules"]) == ["role", "stage", "target"], "S stays in the draft"
    # The pipeline does not write the items again: the open draft holds them.
    assert await _write_draft(p.org_a, [ROLE, TARGET, STAGE]) is None
