"""Unit tests for the NL → rules normalizer (inbox-zero plain-text rule flow).

`_normalize_generated_rules` sanitizes the LLM's JSON into safe rule specs.
Pure function — no LLM/network touched.

The route tests at the end pin EM-T13a (§10.4.15) and its review round 1.
``POST /rules/generate/preview`` returns the specs and saves nothing.
``POST /rules/batch`` saves a list of specs in one session, all or none. The
R8 class runs the batch route on a real database under FORCE RLS.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_rule_generate.py -v -rs
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes import email as m
from pydantic import ValidationError
from sqlalchemy import text

from tests.unit._email_fakes import bind_db
from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_rules = m.automation.rules


def test_single_rule_normalized() -> None:
    out = m._normalize_generated_rules([
        {"name": "Receipts", "instructions": "purchase receipts and invoices",
         "actions": [{"type": "label", "label": "Receipt"}]},
    ])
    assert len(out) == 1
    r = out[0]
    assert r["name"] == "Receipts"
    assert r["instructions"] == "purchase receipts and invoices"
    assert r["conditional_operator"] == "AND"
    assert r["actions"] == [{
        "type": "LABEL", "label": "Receipt", "to_address": None,
        "subject": None, "content": None, "url": None,
    }]


def test_bare_dict_is_wrapped() -> None:
    out = m._normalize_generated_rules(
        {"name": "X", "actions": [{"type": "ARCHIVE"}]})
    assert len(out) == 1 and out[0]["name"] == "X"


def test_multiple_rules() -> None:
    out = m._normalize_generated_rules([
        {"name": "A", "actions": [{"type": "ARCHIVE"}]},
        {"name": "B", "actions": [{"type": "STAR"}]},
    ])
    assert [r["name"] for r in out] == ["A", "B"]


def test_drops_unknown_action_types() -> None:
    out = m._normalize_generated_rules([
        {"name": "A", "actions": [{"type": "NUKE_INBOX"}]},  # invalid → no actions
        {"name": "B", "actions": [{"type": "ARCHIVE"}, {"type": "BOGUS"}]},
    ])
    # "A" dropped (no valid action); "B" keeps only ARCHIVE.
    assert [r["name"] for r in out] == ["B"]
    assert [a["type"] for a in out[0]["actions"]] == ["ARCHIVE"]


def test_drops_specs_without_name() -> None:
    out = m._normalize_generated_rules([
        {"name": "", "actions": [{"type": "ARCHIVE"}]},
        {"actions": [{"type": "ARCHIVE"}]},
    ])
    assert out == []


def test_operator_normalized_to_or() -> None:
    out = m._normalize_generated_rules([
        {"name": "A", "conditional_operator": "or",
         "actions": [{"type": "ARCHIVE"}]},
    ])
    assert out[0]["conditional_operator"] == "OR"


def test_non_list_non_dict_returns_empty() -> None:
    assert m._normalize_generated_rules("nope") == []
    assert m._normalize_generated_rules(None) == []


def test_name_clamped_to_60_chars() -> None:
    out = m._normalize_generated_rules([
        {"name": "x" * 200, "actions": [{"type": "ARCHIVE"}]},
    ])
    assert len(out[0]["name"]) == 60


def test_a_number_from_the_model_becomes_text() -> None:
    """Review round 1 (F2): a number where the route wants a string once made
    a 422 on one spec. The normalizer turns it into text."""
    out = m._normalize_generated_rules([
        {"name": "A", "actions": [{"type": "LABEL", "label": 2024}]},
    ])
    assert out[0]["actions"][0]["label"] == "2024"


def test_an_object_in_a_text_field_drops_the_action() -> None:
    out = m._normalize_generated_rules([
        {"name": "A", "actions": [
            {"type": "FORWARD", "to_address": {"x": "out@evil.test"}},
            {"type": "CALL_WEBHOOK", "url": ["https://hook.test"]},
            {"type": "LABEL", "label": True},
            {"type": "ARCHIVE"},
        ]},
    ])
    assert [a["type"] for a in out[0]["actions"]] == ["ARCHIVE"]


# ── The preview and the batch routes (EM-T13a, review round 1) ──────────────

_SPECS = [{"name": "Copy", "instructions": None, "from_pattern": None,
           "subject_pattern": None, "conditional_operator": "AND",
           "actions": [{"type": "FORWARD", "label": None,
                        "to_address": "out@evil.test", "subject": None,
                        "content": None, "url": None}]}]
_USER = SimpleNamespace(email="u@example.com")


def _route_mocks(insert: AsyncMock):
    load = AsyncMock(return_value=[{"id": "rule-1", "name": "Copy"},
                                   {"id": "rule-2", "name": "Bank"}])
    return (
        patch.object(_rules, "_tenant_session", bind_db(AsyncMock())),
        patch.object(_rules, "_assert_account_owner", AsyncMock()),
        patch.object(_rules, "_llm_generate_rules", AsyncMock(return_value=_SPECS)),
        patch.object(_rules, "_insert_rule", insert),
        patch.object(_rules, "_load_rules", load),
    )


async def _call(route, req):
    insert = AsyncMock(side_effect=["rule-1", "rule-2"])
    a, b, c, d, e = _route_mocks(insert)
    with a, b, c, d, e:
        resp = await route(req, user=_USER)
    return resp, insert


async def test_the_preview_route_returns_the_specs_and_saves_nothing() -> None:
    req = _rules.RuleGenerateRequest(account_id="acc-1", prompt="forward invoices")
    resp, insert = await _call(_rules.preview_generated_rules, req)
    insert.assert_not_awaited()
    assert resp == {"specs": _SPECS}


async def test_the_generate_route_still_saves_each_spec() -> None:
    """The Rules UI calls /rules/generate, and it keeps its contract."""
    req = _rules.RuleGenerateRequest(account_id="acc-1", prompt="forward invoices")
    resp, insert = await _call(_rules.generate_rules, req)
    assert insert.await_count == 1
    assert [r["id"] for r in resp["created"]] == ["rule-1"]
    assert "specs" not in resp


def test_the_generate_request_has_no_preview_field() -> None:
    """One way to preview: the path. A field that an old gateway ignores
    would save the rules with no card (review round 1, F1)."""
    assert "preview" not in _rules.RuleGenerateRequest.model_fields


async def test_the_batch_route_saves_every_spec() -> None:
    req = _rules.RuleBatchRequest(account_id="acc-1", rules=[
        _SPECS[0], {"name": "Bank", "actions": [{"type": "LABEL", "label": "F"}]},
    ])
    resp, insert = await _call(_rules.create_rules_batch, req)
    assert insert.await_count == 2
    assert [r["id"] for r in resp["created"]] == ["rule-1", "rule-2"]


@pytest.mark.parametrize("bad", [
    {"name": "Bank", "actions": [{"type": "LABEL", "label": {"x": 1}}]},
    {"name": "Bank", "actions": [{"type": "LABEL"}], "account_id": "acc-2"},
    {"actions": [{"type": "LABEL"}]},
], ids=["object-label", "account-in-spec", "no-name"])
def test_one_bad_spec_refuses_the_whole_batch(bad) -> None:
    """The body is checked whole, before the first insert runs."""
    with pytest.raises(ValidationError):
        _rules.RuleBatchRequest(account_id="acc-1", rules=[_SPECS[0], bad])


def test_an_empty_or_huge_batch_is_refused() -> None:
    with pytest.raises(ValidationError):
        _rules.RuleBatchRequest(account_id="acc-1", rules=[])
    with pytest.raises(ValidationError):
        _rules.RuleBatchRequest(account_id="acc-1", rules=_SPECS * 51)



def test_an_unknown_key_in_an_action_or_a_batch_is_refused() -> None:
    """Review round 2, F3: the nested models forbid an unknown key."""
    with pytest.raises(ValidationError):
        _rules.RuleActionModel(type="LABEL", label="F", webhook_secret="x")
    with pytest.raises(ValidationError):
        _rules.RuleActionAttachment(path="agent-data/a.pdf", size=10)
    with pytest.raises(ValidationError):
        _rules.RuleBatchRequest(account_id="acc-1", rules=_SPECS, dry_run=True)


def test_an_action_as_the_rules_list_answers_it_still_validates() -> None:
    """The UI and the email assistant send back a rule as GET /rules gave it.
    Each key that ``_load_rules`` writes must stay a field of the model."""
    listed = {"id": "a-1", "type": "DRAFT_EMAIL", "label": None,
              "subject": "Re", "content": "Hi", "to_address": None,
              "cc_address": None, "bcc_address": None, "url": None,
              "delay_minutes": None, "label_ai": False, "content_manual": True,
              "attachments": [{"path": "agent-data/a.pdf", "artifact_id": None,
                               "name": "a.pdf", "ai_selected": False}]}
    model = _rules.RuleActionModel(**listed)
    assert model.model_dump() == listed


# ── R8: the batch route on a real database ──────────────────────────────────


def _seed_account(admin_engine, *, org: str, owner: str) -> str:
    mailbox = f"box-{uuid.uuid4().hex[:8]}@em-t13a.test"
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid)) "
            "RETURNING id"), {"u": owner, "m": mailbox, "o": org}).scalar_one())


def _rule_rows(admin_engine, account_id: str) -> list[tuple[str, str, int]]:
    with admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT r.name, CAST(r.organization_id AS text), "
            "(SELECT COUNT(*) FROM email_actions a WHERE a.rule_id = r.id "
            " AND a.organization_id = r.organization_id) "
            "FROM email_rules r WHERE r.account_id = CAST(:a AS uuid) "
            "ORDER BY r.name"), {"a": account_id}).fetchall()]


@pytest.fixture()
def batch_db(promoted, app_engine):  # noqa: F811
    with app_engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )
    p = promoted
    owner = f"owner-{uuid.uuid4().hex[:8]}@em-t13a.test"
    account_id = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    app_dsn = p.app_url.render_as_string(hide_password=False)

    async def _batch(specs, *, email: str = owner):
        req = _rules.RuleBatchRequest(account_id=account_id, rules=specs)
        user = UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                return await _rules.create_rules_batch(req, user=user)
        finally:
            release_tenant(token)

    try:
        yield SimpleNamespace(batch=_batch, account_id=account_id, org=p.org_b,
                              admin=p.admin_engine)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_rules WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": account_id})
            c.execute(text("DELETE FROM email_accounts WHERE id = "
                           "CAST(:a AS uuid)"), {"a": account_id})


_TWO = [
    {"name": "Bank", "actions": [{"type": "LABEL", "label": "Finance"}]},
    {"name": "Copy", "actions": [
        {"type": "FORWARD", "to_address": "out@evil.test"},
        {"type": "ARCHIVE"}]},
]


@_DB_GATE
class TestTheBatchRouteOnARealDatabase:

    async def test_it_saves_every_rule_in_the_tenant(self, batch_db):
        t = batch_db
        resp = await t.batch(_TWO)
        assert sorted(r["name"] for r in resp["created"]) == ["Bank", "Copy"]
        assert _rule_rows(t.admin, t.account_id) == [
            ("Bank", t.org, 1), ("Copy", t.org, 2)]

    async def test_a_failed_insert_saves_no_rule(self, batch_db, monkeypatch):
        """All or none: the second insert fails, and the first rolls back."""
        t = batch_db
        real = _rules._insert_rule
        calls: list[str] = []

        async def second_fails(db, model):
            calls.append(model.name)
            if len(calls) == 2:
                raise RuntimeError("insert failed")
            return await real(db, model)

        monkeypatch.setattr(_rules, "_insert_rule", second_fails)
        with pytest.raises(RuntimeError):
            await t.batch(_TWO)
        assert calls == ["Bank", "Copy"]
        assert _rule_rows(t.admin, t.account_id) == []

    async def test_another_member_saves_nothing(self, batch_db):
        t = batch_db
        with pytest.raises(HTTPException) as exc:
            await t.batch(_TWO, email="mallory@em-t13a.test")
        assert exc.value.status_code == 404
        assert _rule_rows(t.admin, t.account_id) == []
