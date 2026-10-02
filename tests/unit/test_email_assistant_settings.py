"""Unit tests for the email Assistant settings model.

Guards the serialization contract the Settings UI and the `/assistant/settings`
endpoints depend on. See project-docs/specs/email_inbox_zero_parity_plan.md.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email import AssistantSettingsModel
from gateway.routes.email.automation import assistant as assistant_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def test_settings_default_model_tiers() -> None:
    s = AssistantSettingsModel(account_id="acc-1")
    # auto_run is the global "run rules automatically" switch; defaults ON so a
    # fresh account auto-runs once it has rules (an explicit OFF stops it).
    assert s.auto_run is True
    assert s.cold_email_blocker == "OFF"
    # The task-specific models, each defaulting to its recommended tier. Chat
    # defaults to tier-powerful (strong tool-caller) so chat actions are reliable.
    # No rules model (D-EM-7, EM-T5b-2): the rules run on `decide`.
    assert "rule_model" not in AssistantSettingsModel.model_fields
    assert s.draft_model == "tier-powerful"
    assert s.chat_model == "tier-powerful"
    assert s.digest_frequency == "OFF"
    assert s.about is None
    assert s.signature is None


def test_settings_roundtrip_preserves_overrides() -> None:
    s = AssistantSettingsModel(
        account_id="acc-1",
        about="I run sales at Constellation.",
        signature="— Vijay",
        auto_run=True,
        cold_email_blocker="ARCHIVE",
        # An old client may still send it. It is ignored (D-EM-7).
        rule_model="tier-balanced",
        draft_model="tier-fast",
        chat_model="tier-powerful",
        digest_frequency="DAILY",
    )
    d = s.model_dump()
    assert d["about"] == "I run sales at Constellation."
    assert d["signature"] == "— Vijay"
    assert d["auto_run"] is True
    assert d["cold_email_blocker"] == "ARCHIVE"
    assert "rule_model" not in d
    assert d["draft_model"] == "tier-fast"
    assert d["chat_model"] == "tier-powerful"
    assert d["digest_frequency"] == "DAILY"


def test_inbox_zero_parity_field_defaults() -> None:
    """The migration-29 settings default to inbox-zero's out-of-box behavior —
    with one deliberate divergence: ``follow_up_auto_draft`` starts OFF. Every
    draft is a call on the drafting model, and the follow-up scan releases a
    whole window's backlog on its first working run, so it has to be a switch
    the user turns on. Pinned properly in test_email_auto_draft_defaults.py."""
    s = AssistantSettingsModel(account_id="acc-1")
    assert s.draft_confidence == "ALL_EMAILS"
    assert s.follow_up_awaiting_days == 0
    assert s.follow_up_needs_reply_days == 0
    assert s.follow_up_auto_draft is False
    assert s.digest_categories == []
    assert s.digest_day_of_week == 1
    assert s.digest_time_of_day == "09:00"
    assert s.digest_send_to_email is True


def test_inbox_zero_parity_fields_roundtrip() -> None:
    s = AssistantSettingsModel(
        account_id="acc-1",
        draft_confidence="HIGH_CONFIDENCE",
        follow_up_awaiting_days=5,
        follow_up_needs_reply_days=3,
        follow_up_auto_draft=False,
        digest_categories=["Newsletter", "Cold Emails"],
        digest_frequency="WEEKLY",
        digest_day_of_week=0,
        digest_time_of_day="07:30",
        digest_send_to_email=False,
    )
    d = s.model_dump()
    assert d["draft_confidence"] == "HIGH_CONFIDENCE"
    assert d["follow_up_awaiting_days"] == 5
    assert d["follow_up_needs_reply_days"] == 3
    assert d["follow_up_auto_draft"] is False
    assert d["digest_categories"] == ["Newsletter", "Cold Emails"]
    assert d["digest_day_of_week"] == 0
    assert d["digest_time_of_day"] == "07:30"
    assert d["digest_send_to_email"] is False


# ── EM-T5b-2 item 8: no rules model (D-EM-7) ────────────────────────────────


_ROOT = Path(__file__).resolve().parents[2]


def test_the_account_models_hold_no_rules_model() -> None:
    """No member chooses the rules model. `_account_models` neither offers a
    `rule` key nor reads the stored `rule_model` column."""
    assert "rule" not in assistant_mod._DEFAULT_TASK_MODELS
    assert "rule_model" not in inspect.getsource(assistant_mod._account_models).split(
        '"""')[-1]


def test_the_get_and_put_neither_read_nor_write_the_rules_model() -> None:
    for fn in (assistant_mod.get_assistant_settings, assistant_mod.put_assistant_settings):
        assert "rule_model" not in inspect.getsource(fn), fn.__name__


def test_the_email_agent_tool_takes_no_rules_model() -> None:
    src = (_ROOT / "apps/agents/agent-email-assistant/agents.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
              and n.name == "update_assistant_settings")
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    assert {"draft_model", "chat_model"} <= params
    assert "rule_model" not in params
    assert "rule_model" not in ast.get_source_segment(src, fn)


def test_the_engine_reads_no_account_models() -> None:
    from gateway.routes.email.automation import engine

    for fn in (engine._match_email_to_rule, engine._match_email_to_rules_multi):
        src = inspect.getsource(fn)
        assert "_account_models" not in src and 'models["rule"]' not in src


# ── R8: the stored rule_model stays, and nothing reads it ──────────────────


@_DB_GATE
class TestTheStoredRulesModelUnderForceRls:

    async def test_a_put_keeps_the_stored_value_and_the_get_leaves_it_out(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@settings-t5b.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_assistant_settings (account_id, rule_model, "
                "draft_model, organization_id) VALUES (CAST(:a AS uuid), "
                "'tier-balanced', 'tier-powerful', CAST(:o AS uuid))"),
                {"a": acc, "o": p.org_b})
        user = UserContext(
            email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b,
            access=EffectiveAccess(role_granted=frozenset({"feature:email"})))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                put = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, draft_model="tier-fast",
                                           rule_model="tier-powerful"),
                    user=user)
                got = await assistant_mod.get_assistant_settings(
                    account_id=acc, user=user)
        finally:
            release_tenant(token)
        assert "rule_model" not in put and "rule_model" not in got
        assert got["draft_model"] == "tier-fast"
        with p.admin_engine.connect() as c:
            stored = c.execute(text(
                "SELECT rule_model FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": acc}).scalar_one()
        assert stored == "tier-balanced", "the PUT wrote the rules model column"

