"""EM-T5b-2 (narrowed): the rule match on Jev, ``on``, with no LLM path.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.8, "EM-T5b-2", and
the owner decisions (a) to (d) of 2026-10-02 in §10.2.

The rules these pin:

- ``on`` is accepted for ``email.rule_match`` only. The other three features
  still refuse it (owner decision (c), the demo scope).
- ``DECIDE_FEATURE_ORGS=*`` allows every organization (decision (b)). Empty
  still allows none.
- In ``on`` the Jev answer DECIDES, in both matchers and at every caller, and
  no LLM call is made.
- No fallback (D-EM-8). Every failure leaves the email undecided: the matcher
  raises ``DecisionUnavailable`` (an ``LLMUnavailable``), and no caller stamps
  ``rules_processed_at``. The next cycle asks again.
- Each ``decide`` call names the mailbox owner as a proven member, because a
  deployment Router key refuses a call with no member.
- The AUTOMATIC run touches new mail only (decision (d)). The manual run and
  Process past keep no floor.

Hermetic cases use a FAKE ``acb_llm.decide``. The R8 cases run the real
runner and Process past jobs on a real Postgres, as the non-owner role under
FORCE RLS (the ``promoted`` fixture).

Run (real Postgres for the R8 classes)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_decide_on.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import acb_llm as decide_mod
import pytest
import structlog
from acb_common import get_settings, job_member_scope
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from gateway import decide_features as df
from gateway.routes.email.automation import engine as eng
from gateway.routes.email.automation import runner as runner_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_automation_tenancy import (
    _FakeProvider,
    _patch_providers,
    _seed_message,
)
from tests.unit.test_email_decide_questions import _RULES, EMAIL, FakeDecide
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ORG = "33333333-3333-3333-3333-333333333333"
OTHER_ORG = "44444444-4444-4444-4444-444444444444"
ACC = "acc-on-1"
MID = "msg-on-1"
OWNER = "owner@acme-on.example"

OTHER_FEATURES = ("email.cold_check", "email.sender_pin", "email.thread_status")


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("DECIDE_FEATURE_MODES", "")
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "")
    _clear()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear()


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture()
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


def _modes(monkeypatch, modes: str, orgs: str = "*") -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", orgs)
    _clear()


ON = "email.rule_match=on"


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(decide_mod, "decide", fake)
    return fake


def _llm_tripwire(monkeypatch) -> list[Any]:
    """Any LLM call in `on` is a failure. The list records each one."""
    calls: list[Any] = []

    async def fake(model, messages, **kw):
        calls.append(model)
        return {"index": 1, "reason": "llm"}, "{}", model

    monkeypatch.setattr(eng, "_llm_json", fake)
    return calls


def _records(caps, event: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == event]


# ── Item 1: `on` for the rule match only ───────────────────────────────────


async def test_on_is_accepted_for_the_rule_match(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    assert df.mode_for("email.rule_match") == "on"
    assert frozenset({"email.rule_match"}) == df.ON_FEATURES


@pytest.mark.parametrize("feature", OTHER_FEATURES)
async def test_on_is_still_refused_for_the_other_three(monkeypatch, tenant, feature) -> None:
    with structlog.testing.capture_logs() as caps:
        _modes(monkeypatch, f"{feature}=on,{ON}")
        assert df.mode_for(feature) == "off"
        assert df.mode_for("email.rule_match") == "on"
    rec = _records(caps, "decide.mode_refused")
    assert [(r["decide_feature"], r["decide_reason"]) for r in rec] == [
        (feature, "on_refused")]


# ── Item 2: `*` allows every organization ──────────────────────────────────


async def test_star_allows_every_organization(monkeypatch) -> None:
    _modes(monkeypatch, ON, "*")
    for org in (ORG, OTHER_ORG):
        token = bind_tenant(org)
        try:
            assert df.mode_for("email.rule_match") == "on", org
        finally:
            release_tenant(token)


async def test_star_among_ids_also_allows_every_organization(monkeypatch) -> None:
    _modes(monkeypatch, ON, f"{ORG}, *")
    token = bind_tenant(OTHER_ORG)
    try:
        assert df.mode_for("email.rule_match") == "on"
    finally:
        release_tenant(token)


@pytest.mark.parametrize("orgs", ["", " ", ","])
async def test_an_empty_list_still_allows_no_organization(monkeypatch, tenant, orgs) -> None:
    _modes(monkeypatch, ON, orgs)
    assert df.mode_for("email.rule_match") == "off"


async def test_a_listed_org_is_on_and_another_is_off(monkeypatch) -> None:
    _modes(monkeypatch, ON, ORG)
    token = bind_tenant(OTHER_ORG)
    try:
        assert df.mode_for("email.rule_match") == "off"
    finally:
        release_tenant(token)


async def test_star_with_no_tenant_is_off(monkeypatch) -> None:
    _modes(monkeypatch, ON, "*")
    assert df.mode_for("email.rule_match") == "off"


# ── Item 3: in `on`, the Jev answer decides, and no LLM call is made ───────


async def test_the_single_matcher_uses_the_jev_answer(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    fake = _fake(monkeypatch, by_key={"r0": 0.1, "r1": 0.83}, choices={"best": "r1"})
    llm = _llm_tripwire(monkeypatch)
    out = await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC, message_id=MID)
    assert out == {"index": 1, "reason": "Matched by AI (probability 0.83)."}
    assert len(fake.calls) == 1 and llm == []


async def test_the_single_matcher_reads_no_match_as_none(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    _fake(monkeypatch, p=0.2)
    llm = _llm_tripwire(monkeypatch)
    assert await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC) is None
    assert llm == []


async def test_the_multi_matcher_uses_the_jev_answer(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    _fake(monkeypatch, by_key={"r0": 0.9, "r1": 0.7}, choices={"best": "r1"})
    llm = _llm_tripwire(monkeypatch)
    out = await eng._llm_pick_rules(EMAIL, _RULES, account_id=ACC, message_id=MID)
    assert out == [
        {"index": 0, "reason": "Matched by AI (probability 0.90).", "primary": False},
        {"index": 1, "reason": "Matched by AI (probability 0.70).", "primary": True},
    ]
    assert llm == []


def _engine_env(monkeypatch, rules: list[dict[str, Any]]) -> AsyncMock:
    """The matchers with their loaders patched. The one DB read left is the
    owner read of `_decide_member`."""
    monkeypatch.setattr(eng, "_load_rules", AsyncMock(return_value=[
        {**r, "enabled": True} for r in rules]))
    monkeypatch.setattr(eng, "_load_rule_patterns", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_is_reply_candidate", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(eng, "_load_rule_guidance", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_fetch_sender_history", AsyncMock(return_value=[]))
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(user_id=OWNER)))
    return db


@pytest.mark.parametrize("multi", [False, True])
async def test_classify_matches_decides_on_jev_and_names_the_owner(
    monkeypatch, tenant, multi,
) -> None:
    """The automatic run's path. No run-context member is bound here, as in
    a job with no scope, and the decide call still names the owner, proven."""
    _modes(monkeypatch, ON)
    fake = _fake(monkeypatch, by_key={"r0": 0.95, "r1": 0.1}, choices={"best": "r0"})
    llm = _llm_tripwire(monkeypatch)
    db = _engine_env(monkeypatch, _RULES)
    row = SimpleNamespace(id=MID, thread_id=None)
    out = await eng.classify_matches(db, ACC, row, EMAIL, multi_rule=multi, resolve=False)
    assert [m["rule"]["id"] for m in out] == ["1"]
    assert out[0]["source"] == "ai"
    assert llm == []
    assert fake.calls[0]["member"] == OWNER
    assert fake.calls[0]["member_proven"] is True
    assert fake.calls[0]["module_slug"] == "email"


async def test_the_owner_wins_over_the_request_member(monkeypatch, tenant) -> None:
    """Process past runs as its request member. The decide call still names
    the mailbox owner, read from `email_accounts.user_id`."""
    _modes(monkeypatch, ON)
    fake = _fake(monkeypatch, p=0.9)
    _llm_tripwire(monkeypatch)
    db = _engine_env(monkeypatch, _RULES)
    with job_member_scope("someone.else@acme-on.example", app="email"):
        await eng._match_email_to_rule(db, ACC, EMAIL, message_id=MID)
    assert fake.calls[0]["member"] == OWNER


async def test_outside_on_no_owner_is_read(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow")
    db = AsyncMock()
    assert await eng._decide_member(db, ACC) is None
    db.execute.assert_not_awaited()


async def test_the_rule_test_route_decides_on_jev(monkeypatch, tenant) -> None:
    """The Test route reaches the same matcher, so it shows the Jev answer."""
    _modes(monkeypatch, ON)
    _fake(monkeypatch, by_key={"r0": 0.1, "r1": 0.9}, choices={"best": "r1"})
    llm = _llm_tripwire(monkeypatch)
    db = _engine_env(monkeypatch, _RULES)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(runner_mod, "_tenant_session", session)
    monkeypatch.setattr(runner_mod, "_assert_account_owner", AsyncMock())
    user = SimpleNamespace(email=OWNER)
    out = await runner_mod.test_rules(
        runner_mod.RuleTestRequest(account_id=ACC, subject="Invoice", from_email="a@b.c",
                                   body="Your receipt"), user=user)
    assert out["matched"] is True and out["rule"]["id"] == "2"
    assert out["reason"] == "Matched by AI (probability 0.90)."
    assert llm == []


async def test_shadow_still_acts_on_the_old_answer(monkeypatch, tenant) -> None:
    """`on` changes nothing below it: in `shadow` the LLM answer still acts."""
    _modes(monkeypatch, "email.rule_match=shadow")
    _fake(monkeypatch, p=0.9, choices={"best": "r0"})
    llm = _llm_tripwire(monkeypatch)
    out = await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC)
    assert out == {"index": 1, "reason": "llm"} and len(llm) == 1


# ── Item 4: no fallback. No decision means undecided ───────────────────────


class _Slow:
    async def __call__(self, state, questions, **kw):
        await asyncio.sleep(5.0)


class _NotADecision:
    async def __call__(self, state, questions, **kw):
        return {"answers": "no"}


class _MissingAnswer:
    async def __call__(self, state, questions, **kw):
        return decide_mod.Decision(answers=MappingProxyType({}), request_id="r")


FAILURES = {
    "unavailable": (lambda: FakeDecide(raises=decide_mod.DecideUnavailable("HTTP 503")),
                    "HTTP 503"),
    "invalid": (lambda: FakeDecide(raises=decide_mod.DecideRequestInvalid(
        400, {"reason": "too_many_questions"})), "request_invalid"),
    "timeout": (_Slow, "timeout"),
    # The merge reads `.answers`, so the reason is the error type.
    "not_a_decision": (_NotADecision, "AttributeError"),
    "unreadable": (_MissingAnswer, "unreadable:KeyError"),
}


@pytest.mark.parametrize("kind", list(FAILURES))
@pytest.mark.parametrize("multi", [False, True])
async def test_no_decision_raises_and_calls_no_llm(monkeypatch, tenant, kind, multi) -> None:
    _modes(monkeypatch, ON)
    monkeypatch.setattr(df, "ON_BOUND_S", 0.2)
    make, reason = FAILURES[kind]
    monkeypatch.setattr(decide_mod, "decide", make())
    llm = _llm_tripwire(monkeypatch)
    pick = eng._llm_pick_rules if multi else eng._llm_pick_rule
    with structlog.testing.capture_logs() as caps, \
            pytest.raises(eng.DecisionUnavailable) as raised:
        await pick(EMAIL, _RULES, account_id=ACC, message_id=MID)
    assert isinstance(raised.value, eng.LLMUnavailable)
    assert llm == [], "no LLM call replaces a missing decision (D-EM-8)"
    rec = _records(caps, "decide.unavailable")
    assert len(rec) == 1
    assert (rec[0]["decide_feature"], rec[0]["account_id"], rec[0]["message_id"]) == (
        "email.rule_match", ACC, MID)
    assert reason in rec[0]["decide_reason"]
    assert _records(caps, "decide.decided") == []
    if kind == "invalid":
        (err,) = _records(caps, "decide.request_invalid")
        assert err["log_level"] == "error"


def test_the_on_bound_is_the_spec_client_bound() -> None:
    assert df.ON_BOUND_S == 10.0


# ── Item 9: the decide.decided line ────────────────────────────────────────


async def test_the_decided_line_holds_keys_and_the_message_id(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    _fake(monkeypatch, by_key={"r0": 0.9, "r1": 0.2}, choices={"best": "r0"})
    secret = {**EMAIL, "subject": "SECRET-SUBJ", "body": "SECRET-BODY",
              "from": "secret@vendor.example"}
    rules = [{**_RULES[0], "name": "SECRET-RULE"}, _RULES[1]]
    with structlog.testing.capture_logs() as caps:
        await eng._llm_pick_rule(secret, rules, account_id=ACC, message_id=MID)
    (rec,) = _records(caps, "decide.decided")
    assert rec["message_id"] == MID and rec["account_id"] == ACC
    assert rec["matched"] == ["r0"] and rec["main"] == "r0" and rec["p_r0"] == 0.9
    assert rec["requests"] == 1 and rec["request_ids"] == ["req-1"]
    assert isinstance(rec["latency_ms"], int)
    text_ = repr([c for c in caps if c["event"].startswith("decide.")])
    for s in ("SECRET-SUBJ", "SECRET-BODY", "secret@vendor.example", "SECRET-RULE"):
        assert s not in text_


# ── R8: the automatic run and Process past, on a real database ─────────────


def _seed_rule(admin, *, org: str, account_id: str, name: str, instructions: str,
               created_at: datetime, enabled: bool = True) -> str:
    with admin.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, instructions, enabled, "
            "created_at, organization_id) VALUES (CAST(:a AS uuid), :n, :i, :e, "
            ":c, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "n": name, "i": instructions, "e": enabled,
             "c": created_at, "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, organization_id) "
            "VALUES (CAST(:r AS uuid), 'LABEL', :n, CAST(:o AS uuid))"),
            {"r": rid, "n": name, "o": org})
    return rid


def _stamps(admin, account_id: str) -> dict[str, Any]:
    with admin.connect() as c:
        rows = c.execute(text(
            "SELECT id::text AS id, rules_processed_at FROM email_messages "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).mappings().all()
    return {r["id"]: r["rules_processed_at"] for r in rows}


def _logged(admin, account_id: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT message_id::text AS mid, status, rule_name, match_source "
            "FROM email_executed_rules WHERE account_id = CAST(:a AS uuid)"),
            {"a": account_id}).mappings().all())


@asynccontextmanager
async def _as_app(p, org: str):
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            yield
    finally:
        release_tenant(token)


def _two_messages(p, *, rule_age: timedelta) -> tuple[str, str, str, str]:
    """A mailbox of org B with one rule, one message from before the rule
    and one after it. Returns (account, owner, old message, new message)."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    now = datetime.now(UTC)
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
               instructions="Receipts and invoices.", created_at=now - rule_age)
    old = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                        thread_id=f"t-old-{acc}", received_at=now - rule_age - timedelta(hours=1))
    new = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                        thread_id=f"t-new-{acc}", received_at=now - timedelta(minutes=5))
    return acc, owner, old, new


@_DB_GATE
class TestTheAutomaticRunOnJev:

    async def test_it_decides_new_mail_as_the_owner_and_skips_older_mail(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Items 3, 5 and 7: the scheduler run decides the message from after
        the rule on Jev, names the owner with no job scope, applies the rule,
        and never selects the message from before the rule."""
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        llm = _llm_tripwire(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert len(fake.calls) == 1, "the run decided the older message too"
        assert fake.calls[0]["member"] == owner
        assert fake.calls[0]["member_proven"] is True
        assert llm == []
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[new] is not None and stamps[old] is None
        logs = _logged(p.admin_engine, acc)
        assert [(r["mid"], r["status"], r["rule_name"], r["match_source"]) for r in logs] \
            == [(new, "APPLIED", "Receipt", "ai")]

    async def test_the_manual_run_has_no_new_mail_floor(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        acc, owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, owner)
        assert len(fake.calls) == 2
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[new] is not None and stamps[old] is not None

    async def test_an_undecided_email_is_not_stamped(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Item 4 on the automatic run: Jev is down, no LLM call replaces it,
        and the message stays unstamped for the next cycle."""
        p = promoted
        acc, _owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
        llm = _llm_tripwire(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        with structlog.testing.capture_logs() as caps:
            async with _as_app(p, p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert llm == []
        assert _stamps(p.admin_engine, acc) == {old: None, new: None}
        assert _logged(p.admin_engine, acc) == []
        assert [r["message_id"] for r in _records(caps, "email.classify_unavailable_skip")] \
            == [new]


@_DB_GATE
class TestProcessPastOnJev:

    async def _run(self, p, acc: str, owner: str) -> dict[str, Any]:
        import email_ingestion.scheduler as sched_mod

        now = datetime.now(UTC)
        token = runner_mod._past_job_start(acc, owner, 0, False, downloading=True)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sched_mod, "_sync_account", AsyncMock(return_value={}))
            async with _as_app(p, p.org_b):
                await runner_mod._process_past_emails_job(
                    acc, now - timedelta(days=7), now + timedelta(days=1), 50,
                    False, owner, job_token=token)
        return runner_mod._PAST_JOBS.pop(acc)

    async def test_process_past_decides_older_mail_on_jev(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Older mail changes only through Process past (decision (d)), and
        it decides on Jev, as the owner."""
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        llm = _llm_tripwire(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        await self._run(p, acc, owner)
        assert len(fake.calls) == 2 and llm == []
        assert {c["member"] for c in fake.calls} == {owner}
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[old] is not None and stamps[new] is not None
        assert {r["rule_name"] for r in _logged(p.admin_engine, acc)} == {"Receipt"}

    async def test_an_undecided_email_is_not_stamped_by_process_past(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        acc, owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
        llm = _llm_tripwire(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        with structlog.testing.capture_logs() as caps:
            await self._run(p, acc, owner)
        assert llm == []
        assert _stamps(p.admin_engine, acc) == {old: None, new: None}
        assert _logged(p.admin_engine, acc) == []
        skipped = _records(caps, "email.process_past_classify_unavailable_skip")
        assert sorted(r["message_id"] for r in skipped) == sorted([old, new])


@_DB_GATE
class TestTheNewMailFloorSql:
    """Item 7, the phase-0 SELECT of the REAL job, as the non-owner role
    under FORCE RLS. A dry run with `classify_matches` recording each row it
    gets shows which rows the SELECT returned, newest first."""

    async def _select(self, p, acc: str, *, scheduler: bool) -> list[str]:
        seen: list[str] = []

        async def record(db, account_id, row, email, **kw):
            seen.append(str(row.id))
            return []

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(runner_mod, "classify_matches", record)
            async with _as_app(p, p.org_b):
                await runner_mod._run_rules_job(
                    acc, 50, True, "scheduler" if scheduler else "member@decide-on.test")
        return seen

    async def test_older_mail_is_not_selected_and_newer_mail_is(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc, _owner, old, new = _two_messages(p, rule_age=timedelta(hours=1))
        assert await self._select(p, acc, scheduler=True) == [new]
        assert await self._select(p, acc, scheduler=False) == [new, old]

    async def test_a_disabled_older_rule_does_not_move_the_floor(
        self, promoted, app_engine,  # noqa: F811
    ):
        p = promoted
        acc, _owner, _old, new = _two_messages(p, rule_age=timedelta(hours=1))
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Old",
                   instructions="x", created_at=datetime.now(UTC) - timedelta(days=30),
                   enabled=False)
        assert await self._select(p, acc, scheduler=True) == [new]

    async def test_with_no_enabled_rule_the_automatic_run_selects_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=f"t-{acc}")
        assert await self._select(p, acc, scheduler=True) == []
        assert len(await self._select(p, acc, scheduler=False)) == 1


async def test_the_floor_is_the_scheduler_only(monkeypatch) -> None:
    """The text of the floor goes into the SQL only for the scheduler, and it
    holds no value from a request."""
    assert ":aid" in runner_mod._NEW_MAIL_ONLY and "{" not in runner_mod._NEW_MAIL_ONLY
    assert "MIN(r.created_at)" in runner_mod._NEW_MAIL_ONLY
    assert runner_mod._SCHEDULER == "scheduler"
    seen: list[str] = []

    @asynccontextmanager
    async def session():
        db = AsyncMock()

        async def execute(stmt, params=None):
            seen.append(str(stmt))
            return MagicMock(fetchall=MagicMock(return_value=[]))

        db.execute = execute
        yield db

    monkeypatch.setattr(runner_mod, "_tenant_session", session)
    await runner_mod._run_rules_job("acc", 50, False, "scheduler")
    await runner_mod._run_rules_job("acc", 50, False, "member@acme.example")
    assert "MIN(r.created_at)" in seen[0]
    assert "MIN(r.created_at)" not in seen[1]

