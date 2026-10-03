"""EM-T5b-2: the four triage decisions on Jev, ``on``, with no LLM path.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.8, "EM-T5b-2", and
the owner decisions (a) to (d) of 2026-10-02 in §10.2. The narrowed slice
(#576) opened ``on`` for the rule match. EM-T5b-2 in full opens the thread
status, the cold check and the sender pin.

The rules these pin:

- ``on`` is accepted for the four email features. A name outside them still
  resolves to ``off`` and logs ``decide.mode_refused``.
- The thread status: the choice decides, a decided status never gets
  ``· auto``, and with no decision nothing is written. Inside
  ``classify_matches`` the runner then skips the row, and
  ``recompute_thread_status`` returns None, so the labels stay. Fix round
  3: a status whose conversation rule moves mail needs 0.7. The status is
  asked before the rule match when it is sure to be asked, and not at all
  when the mailbox has no enabled conversation rule.
- The cold check: cold at 0.5 or above only, and at 0.7 or above when the
  blocker archives (fix round 3). With no decision the email is not cold,
  and the rule outcome stands, so the runner stamps it.
- The sender pin: a pin at 0.9 or above only. With no decision, no pin.
- The startup check logs ``email.decide_not_wired`` when a feature is ``on``
  and the box cannot reach ``decide``.
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
  Process past keep no floor. The Reply Zero backfill keeps the floor for
  its inbox and its sent threads (fix round 3).

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
from gateway.routes.email import scheduler_hooks as hooks_mod
from gateway.routes.email.automation import engine as eng
from gateway.routes.email.automation import learning as lrn
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import runner as runner_mod
from gateway.routes.email.automation import senders as snd
from sqlalchemy import text

from tests.unit._sql_match import hits
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_automation_tenancy import (
    _FakeProvider,
    _patch_providers,
    _seed_message,
    _seed_settings,
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

#: Every email feature in `on`, the value the orchestrator sets on the box.
ALL_ON = ",".join(f"{f}=on" for f in df.FEATURES)


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
    df._cooldown_until.clear()


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


# ── Item 1: `on` for the four email features ───────────────────────────────


@pytest.mark.parametrize("feature", df.FEATURES)
async def test_on_is_accepted_for_each_email_feature(monkeypatch, tenant, feature) -> None:
    with structlog.testing.capture_logs() as caps:
        _modes(monkeypatch, f"{feature}=on")
        assert df.mode_for(feature) == "on"
    assert _records(caps, "decide.mode_refused") == []
    assert frozenset(df.FEATURES) == df.ON_FEATURES


async def test_the_orchestrator_value_turns_all_four_on(monkeypatch, tenant) -> None:
    assert ALL_ON == ("email.cold_check=on,email.sender_pin=on,"
                      "email.thread_status=on,email.rule_match=on")
    _modes(monkeypatch, ALL_ON)
    assert {f: df.mode_for(f) for f in df.FEATURES} == dict.fromkeys(df.FEATURES, "on")


@pytest.mark.parametrize("name", ["email.cold_sender", "email.pin", "email.rule_pick"])
async def test_on_for_a_name_outside_the_four_is_refused(monkeypatch, tenant, name) -> None:
    with structlog.testing.capture_logs() as caps:
        _modes(monkeypatch, f"{name}=on,{ON}")
        assert df.mode_for(name) == "off"
        assert df.mode_for("email.rule_match") == "on"
    rec = _records(caps, "decide.mode_refused")
    assert [(r["decide_feature"], r["decide_reason"]) for r in rec] == [(name, "unknown")]


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
    return _owner_db()


def _owner_db(account_id: str = ACC) -> AsyncMock:
    """A DB that answers ONLY `SELECT user_id FROM email_accounts WHERE id =
    :aid` for `account_id`. Any other read gets no row, so a read of a wrong
    column, table or account fails the owner tests (fix round 2, item 7c)."""

    async def execute(stmt, params=None):
        sql = " ".join(str(stmt).split())
        owner_read = (hits(sql, "FROM email_accounts")
                      and sql.startswith("SELECT user_id FROM email_accounts")
                      and (params or {}).get("aid") == account_id)
        row = SimpleNamespace(user_id=OWNER) if owner_read else None
        return MagicMock(fetchone=MagicMock(return_value=row))

    db = AsyncMock()
    db.execute = execute
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



# ── Fix round 2 ─────────────────────────────────────────────────────────────


def _choice(key: str, probabilities: dict[str, float]) -> Any:
    return decide_mod.ChoiceAnswer(
        choice=key, probabilities=MappingProxyType(probabilities), confidence=0.5)


def _conv_rules(done_actions: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Four conversation rules (r0 to r3) and one cleanup rule (r4)."""
    names = [("Needs Reply", "REPLY"), ("Awaiting Reply", "AWAITING_REPLY"),
             ("Done", "DONE"), ("FYI", "FYI")]
    rules = [{"id": f"v{i}", "name": n, "instructions": "x", "system_type": k,
              "actions": done_actions if n == "Done" else [{"type": "LABEL"}]}
             for i, (n, k) in enumerate(names)]
    return [*rules, {"id": "c", "name": "Receipt", "instructions": "x",
                     "actions": [{"type": "LABEL"}]}]


def _decision_of(answers: dict[str, Any]) -> Any:
    return decide_mod.Decision(answers=MappingProxyType(answers), request_id="r")


@pytest.mark.parametrize(("p_done", "matched"), [(0.6, False), (0.69, False),
                                                 (0.7, True), (0.9, True)])
def test_a_moving_conversation_rule_needs_the_move_bar(p_done, matched) -> None:
    """Item 1: "Done" with ARCHIVE moves mail, so the `conv` pick needs 0.7
    on its own option, as a moving boolean does."""
    rules = _conv_rules([{"type": "LABEL"}, {"type": "ARCHIVE"}])
    decision = _decision_of({
        "conv": _choice("r2", {"r0": 0.1, "r2": p_done, "none": 0.1}),
        "r4": decide_mod.BooleanAnswer(probability=0.1),
        "best": _choice("none", {"none": 0.9})})
    reading = eng._read_rule_match(decision, rules)
    assert (2 in reading.matched) is matched


def test_a_label_only_conversation_rule_keeps_the_plurality() -> None:
    rules = _conv_rules([{"type": "LABEL"}])
    decision = _decision_of({
        "conv": _choice("r3", {"r0": 0.25, "r1": 0.2, "r3": 0.3, "none": 0.25}),
        "r4": decide_mod.BooleanAnswer(probability=0.1),
        "best": _choice("none", {"none": 0.9})})
    assert eng._read_rule_match(decision, rules).matched == (3,)


def _movers() -> list[dict[str, Any]]:
    return [{"id": f"m{i}", "name": f"Mover {i}", "instructions": "x",
             "actions": [{"type": "MOVE_FOLDER", "label": f"F{i}"}]} for i in range(3)]


@pytest.mark.parametrize(("probs", "kept"), [
    ({"r0": 0.8, "r1": 0.9, "r2": 0.1}, 1),
    ({"r0": 0.85, "r1": 0.85, "r2": 0.9}, 2),
    ({"r0": 0.8, "r1": 0.8, "r2": 0.1}, 0),
])
def test_at_most_one_moving_rule_matches(probs, kept) -> None:
    """Item 4: two moves on one email would act on a stale provider id. The
    most probable mover stays, and a tie goes to the canonical order."""
    decision = _decision_of({
        **{k: decide_mod.BooleanAnswer(probability=v) for k, v in probs.items()},
        "best": _choice("none", {"none": 0.9})})
    reading = eng._read_rule_match(decision, _movers())
    assert reading.matched == (kept,)
    assert [p["index"] for p in reading.as_picks()] == [kept]
    assert len(reading.fields["dropped_moves"]) >= 1


def test_a_label_rule_still_rides_with_one_mover() -> None:
    rules = [*_movers()[:2], {"id": "l", "name": "Label", "instructions": "x",
                              "actions": [{"type": "LABEL"}]}]
    decision = _decision_of({
        "r0": decide_mod.BooleanAnswer(probability=0.9),
        "r1": decide_mod.BooleanAnswer(probability=0.8),
        "r2": decide_mod.BooleanAnswer(probability=0.6),
        "best": _choice("r1", {"r1": 0.9})})
    reading = eng._read_rule_match(decision, rules)
    assert reading.matched == (0, 2)
    assert reading.main == 0  # `best` named the dropped mover


async def test_a_402_starts_a_cooldown_with_no_router_calls(monkeypatch, tenant) -> None:
    """Item 3: after a 402, no Router call for 15 minutes. Each email stays
    undecided with the reason `cooldown`. After the window, one call."""
    _modes(monkeypatch, ON)
    clock = [1000.0]
    monkeypatch.setattr(df, "_now", lambda: clock[0])
    fake = _fake(monkeypatch, raises=decide_mod.DecideUnavailable(
        "insufficient_credits", status=402))
    with structlog.testing.capture_logs() as caps:
        with pytest.raises(eng.DecisionUnavailable):
            await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC, message_id=MID)
        assert len(fake.calls) == 1
        clock[0] += df.REFUSAL_COOLDOWN_S - 1
        with pytest.raises(eng.DecisionUnavailable):
            await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC, message_id="m2")
        assert len(fake.calls) == 1, "a call ran inside the cool-down"
    reasons = [r["decide_reason"] for r in _records(caps, "decide.unavailable")]
    assert reasons == ["insufficient_credits", "cooldown"]
    assert _records(caps, "decide.cooldown_started")[0]["decide_status"] == 402
    clock[0] += 2
    with pytest.raises(eng.DecisionUnavailable):
        await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC)
    assert len(fake.calls) == 2


async def test_a_403_cools_only_its_own_organization(monkeypatch) -> None:
    _modes(monkeypatch, ON, "*")
    _fake(monkeypatch, raises=decide_mod.DecideUnavailable("forbidden", status=403))
    token = bind_tenant(ORG)
    try:
        with pytest.raises(eng.DecisionUnavailable):
            await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC)
    finally:
        release_tenant(token)
    fake = _fake(monkeypatch, p=0.9)
    token = bind_tenant(OTHER_ORG)
    try:
        assert await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC) is not None
    finally:
        release_tenant(token)
    assert len(fake.calls) == 1


async def test_an_outage_without_a_verdict_starts_no_cooldown(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ON)
    fake = _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503", status=503))
    for _ in range(2):
        with pytest.raises(eng.DecisionUnavailable):
            await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("mode", ["", "email.rule_match=shadow"])
@pytest.mark.parametrize("multi", [False, True])
async def test_off_and_shadow_run_the_old_rule_call_on_tier_fast(
    monkeypatch, tenant, mode, multi,
) -> None:
    """Item 8 (verifier F5): no member chooses the rules model, so the old
    call that still runs outside `on` uses `tier-fast`."""
    _modes(monkeypatch, mode)
    _fake(monkeypatch, p=0.9)
    models: list[str] = []

    async def fake_llm(model, messages, **kw):
        models.append(model)
        return ({"matches": []} if multi else {"index": -1}), "{}", model

    monkeypatch.setattr(eng, "_llm_json", fake_llm)
    db = _engine_env(monkeypatch, _RULES)
    fn = eng._match_email_to_rules_multi if multi else eng._match_email_to_rule
    await fn(db, ACC, EMAIL, message_id=MID)
    assert models == ["tier-fast"]


# Item 5: the single-message re-run, with no second answer.


async def test_the_rerun_applies_nothing_with_no_second_answer(monkeypatch, tenant) -> None:
    """`run_rules_on_message`: the first answer matched, the second raises.
    The spec says: apply nothing and stamp nothing (§10.4.8 EM-T5b-2 item 6)."""
    row = SimpleNamespace(
        id=MID, provider_message_id="pm", thread_id="t", subject="s", body_text="b",
        snippet="", from_address={"email": "a@b.c"}, to_addresses=[],
        cc_addresses=[], received_at=None)

    async def execute(stmt, params=None):
        sql = " ".join(str(stmt).split())
        if hits(sql, "FROM email_messages"):
            return MagicMock(fetchone=MagicMock(return_value=row))
        if hits(sql, "FROM email_accounts"):
            return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                provider="microsoft", credentials_encrypted="x", user_id=OWNER)))
        return MagicMock(fetchone=MagicMock(return_value=None))

    db = AsyncMock()
    db.execute = execute

    @asynccontextmanager
    async def session():
        yield db

    provider = _FakeProvider()
    _patch_providers(monkeypatch, provider)
    monkeypatch.setattr(runner_mod, "_tenant_session", session)
    monkeypatch.setattr(runner_mod, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(runner_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(runner_mod, "resolve_org_domains", AsyncMock(return_value=set()))
    monkeypatch.setattr(runner_mod, "_attachment_summaries", AsyncMock(return_value={}))
    monkeypatch.setattr(runner_mod, "_account_self_email", AsyncMock(return_value=OWNER))
    first = {"rule": {"id": "1", "name": "Receipt", "actions": []}, "reason": "r",
             "source": "ai"}
    monkeypatch.setattr(runner_mod, "_match_email_to_rule", AsyncMock(return_value=first))
    monkeypatch.setattr(runner_mod, "classify_matches",
                        AsyncMock(side_effect=eng.DecisionUnavailable("no answer")))
    applied = AsyncMock()
    stamped = AsyncMock()
    monkeypatch.setattr(runner_mod, "_apply_matches", applied)
    monkeypatch.setattr(runner_mod, "_stamp_processed_watermark", stamped)
    out = await runner_mod.run_rules_on_message(
        runner_mod.RuleRunMessageRequest(account_id=ACC, message_id=MID, is_test=False),
        user=SimpleNamespace(email=OWNER))
    assert out["applied"] is False and out["unavailable"] is True
    applied.assert_not_awaited()
    stamped.assert_not_awaited()


# Item 2 (and 7a): the Reply Zero backfill keeps the new-mail floor.


@_DB_GATE
class TestTheReplyZeroBackfillOnJev:

    async def test_the_backfill_decides_new_inbox_threads_only(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """In `on`, `_maybe_classify_threads` reaches Jev through
        `classify_matches`. It decides the thread whose latest message came
        after the first enabled rule, as the owner, and it writes nothing for
        the older thread."""
        from gateway.routes.email.automation import replyzero as rz

        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, _old, _new = _two_messages(p, rule_age=timedelta(hours=1))
        _modes(monkeypatch, ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        llm = _llm_tripwire(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)
        assert len(fake.calls) == 1, "the backfill decided the older thread too"
        assert fake.calls[0]["member"] == owner
        assert llm == []
        with p.admin_engine.connect() as c:
            threads = {r.thread_id for r in c.execute(text(
                "SELECT thread_id FROM email_thread_status "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": acc})}
        assert threads == {f"t-new-{acc}"}

    async def test_the_backfill_selects_no_inbox_thread_without_an_enabled_rule(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        from gateway.routes.email.automation import replyzero as rz

        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=f"t-{acc}")
        _modes(monkeypatch, ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)
        assert fake.calls == []
        with p.admin_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM email_thread_status "
                               "WHERE account_id = CAST(:a AS uuid)"), {"a": acc}).scalar()
        assert n == 0

    async def test_the_backfill_decides_new_sent_threads_only(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 3, fix 5: a SENT gap thread keeps the floor too. Each one
        costs a status call and writes a status and labels, so the thread
        from before the first enabled rule is not selected."""
        from gateway.routes.email.automation import replyzero as rz

        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = datetime.now(UTC)
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
                   instructions="Receipts and invoices.",
                   created_at=now - timedelta(hours=1))
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      thread_id=f"t-sent-old-{acc}", received_at=now - timedelta(hours=2))
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      thread_id=f"t-sent-new-{acc}", received_at=now - timedelta(minutes=5))
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, choices={"status": "AWAITING_REPLY"})
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)
        assert _asked(fake) == ["status"], "the backfill decided the older sent thread"
        assert fake.calls[0]["member"] == owner and llm == []
        assert [(r["thread_id"], r["status"]) for r in _status_rows(p.admin_engine, acc)] \
            == [(f"t-sent-new-{acc}", "AWAITING")]

    async def test_the_backfill_selects_no_sent_thread_without_an_enabled_rule(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        from gateway.routes.email.automation import replyzero as rz

        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      thread_id=f"t-sent-{acc}")
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, choices={"status": "AWAITING_REPLY"})
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)
        assert fake.calls == []
        assert _status_rows(p.admin_engine, acc) == []


# ═══ EM-T5b-2 in full: the thread status, the cold check, the sender pin ═══

#: The thread as facts (`ThreadContext.messages`), the other party last.
THREAD = [{
    "side": "other_party", "from": EMAIL["from"], "to": "owner@acme.com",
    "cc": "", "owner_cc_only": False, "date": "", "subject": EMAIL["subject"],
    "attachments": "", "body": EMAIL["body"],
}]

SENDER = "news@list.example"
PIN_RULE = {"id": "n1", "name": "Newsletter", "instructions": "newsletters"}

#: The tenant text of the fixtures. No `decide.*` line may hold any of it.
SECRETS = (EMAIL["subject"], EMAIL["body"], EMAIL["from"], "SECRET-SNIPPET")


def _llm_tripwire_all(monkeypatch) -> list[Any]:
    """Any LLM call at any of the four sites, in `on`, is a failure."""
    calls: list[Any] = []

    async def fake(model, messages, **kw):
        calls.append(model)
        return {}, "{}", model

    for module in (eng, rz, snd, lrn):
        monkeypatch.setattr(module, "_llm_json", fake)
    return calls


class _FailOn(FakeDecide):
    """Answers as FakeDecide does, and fails each request that asks `qid`."""

    def __init__(self, qid: str, exc: BaseException, **kw) -> None:
        super().__init__(**kw)
        self.qid = qid
        self.exc = exc

    async def __call__(self, state, questions, **kwargs):
        if self.qid in questions:
            self.calls.append({"state": state, "questions": questions, **kwargs})
            raise self.exc
        return await super().__call__(state, questions, **kwargs)


def _call_for(fake: FakeDecide, qid: str) -> dict[str, Any]:
    (call,) = [c for c in fake.calls if qid in c["questions"]]
    return call


def _site_db(*, recent: int = 4) -> AsyncMock:
    """A DB for the cold gate and the pin. It answers the owner read for ACC
    only, `recent` messages of the sender, and nothing else. `db.sql` keeps
    every statement, so a test can see each write."""
    sql_seen: list[str] = []

    async def execute(stmt, params=None):
        sql = " ".join(str(stmt).split())
        sql_seen.append(sql)
        if (sql.startswith("SELECT user_id FROM email_accounts")
                and (params or {}).get("aid") == ACC):
            return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(user_id=OWNER)))
        if sql.startswith("SELECT subject, snippet FROM email_messages"):
            rows = [SimpleNamespace(subject=EMAIL["subject"], snippet="SECRET-SNIPPET")]
            return MagicMock(fetchall=MagicMock(return_value=rows * recent))
        return MagicMock(fetchone=MagicMock(return_value=None),
                         fetchall=MagicMock(return_value=[]))

    db = AsyncMock()
    db.execute = execute
    db.sql = sql_seen
    return db


async def _status(*, user_sent_last: bool = False, member: str | None = None):
    return await rz._llm_determine_thread_status(
        "thread text", OWNER, "", user_sent_last=user_sent_last,
        account_id=ACC, thread_messages=THREAD, message_id=MID, member=member)


async def _cold(blocker: str = "LABEL"):
    return await snd._llm_is_cold(
        EMAIL, blocker=blocker, account_id=ACC, message_id=MID)


async def _pin(db: Any = None):
    return await lrn._ai_confirms_sender_pattern(
        db if db is not None else _site_db(), ACC, SENDER, PIN_RULE, message_id=MID)


#: What each site gives with no decision (§10.4.8 EM-T5b-2 item 6).
NO_DECISION: dict[str, Any] = {
    "email.thread_status": eng.DecisionUnavailable,
    "email.cold_check": (False, ""),
    "email.sender_pin": False,
}
SITE_CALLS = {"email.thread_status": _status, "email.cold_check": _cold,
              "email.sender_pin": _pin}


# ── Item 3: in `on`, the Jev answer decides each site ──────────────────────


@pytest.mark.parametrize(("user_sent_last", "choice"), [
    (False, "FYI"), (False, "REPLY"), (True, "DONE"), (True, "AWAITING_REPLY")])
async def test_the_thread_status_uses_the_jev_answer(
    monkeypatch, tenant, user_sent_last, choice,
) -> None:
    _modes(monkeypatch, "email.thread_status=on")
    fake = _fake(monkeypatch, choices={"status": choice})
    llm = _llm_tripwire_all(monkeypatch)
    assert await _status(user_sent_last=user_sent_last) == (choice, True)
    assert llm == [] and len(fake.calls) == 1
    options = set(fake.calls[0]["questions"]["status"].criteria)
    assert ("FYI" in options) is (not user_sent_last)


async def test_a_status_outside_the_options_is_no_decision(monkeypatch, tenant) -> None:
    """The owner's side sent last, so FYI is not an option. An answer of FYI
    is not ours to act on."""
    _modes(monkeypatch, "email.thread_status=on")
    _fake(monkeypatch, choices={"status": "FYI"})
    with structlog.testing.capture_logs() as caps, \
            pytest.raises(eng.DecisionUnavailable):
        await _status(user_sent_last=True)
    (rec,) = _records(caps, "decide.unavailable")
    assert rec["decide_reason"] == "unreadable:ValueError"


@pytest.mark.parametrize(("p", "cold"), [(0.49, False), (0.5, True), (0.9, True)])
async def test_the_cold_check_acts_only_at_its_bar(monkeypatch, tenant, p, cold) -> None:
    _modes(monkeypatch, "email.cold_check=on")
    _fake(monkeypatch, by_key={"cold": p})
    llm = _llm_tripwire_all(monkeypatch)
    verdict = await _cold()
    assert verdict[0] is cold and llm == []
    assert verdict[1] == (f"Cold outreach by AI (probability {p:.2f})." if cold else "")


@pytest.mark.parametrize(("p", "pin"), [(0.5, False), (0.89, False), (0.9, True), (0.97, True)])
async def test_the_pin_acts_only_at_ninety_percent(monkeypatch, tenant, p, pin) -> None:
    _modes(monkeypatch, "email.sender_pin=on")
    _fake(monkeypatch, by_key={"always": p})
    llm = _llm_tripwire_all(monkeypatch)
    assert await _pin() is pin and llm == []


async def test_the_pin_still_needs_three_messages(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.sender_pin=on")
    fake = _fake(monkeypatch, by_key={"always": 0.99})
    assert await _pin(_site_db(recent=2)) is False
    assert fake.calls == []


# ── Items 3 and 6: no decision, no fallback, and nothing written ───────────


@pytest.mark.parametrize("kind", list(FAILURES))
@pytest.mark.parametrize("feature", list(SITE_CALLS))
async def test_no_decision_leaves_each_site_undecided_with_no_llm(
    monkeypatch, tenant, feature, kind,
) -> None:
    _modes(monkeypatch, f"{feature}=on")
    monkeypatch.setattr(df, "ON_BOUND_S", 0.2)
    make, reason = FAILURES[kind]
    monkeypatch.setattr(decide_mod, "decide", make())
    llm = _llm_tripwire_all(monkeypatch)
    expected = NO_DECISION[feature]
    with structlog.testing.capture_logs() as caps:
        if expected is eng.DecisionUnavailable:
            with pytest.raises(eng.DecisionUnavailable) as raised:
                await SITE_CALLS[feature]()
            assert isinstance(raised.value, eng.LLMUnavailable)
        else:
            assert await SITE_CALLS[feature]() == expected
    assert llm == [], "no LLM call replaces a missing decision (D-EM-8)"
    (rec,) = _records(caps, "decide.unavailable")
    assert (rec["decide_feature"], rec["account_id"], rec["message_id"]) == (feature, ACC, MID)
    assert reason in rec["decide_reason"]
    assert _records(caps, "decide.decided") == []
    if kind == "invalid":
        (err,) = _records(caps, "decide.request_invalid")
        assert err["log_level"] == "error"


def _status_env(monkeypatch, *, our_side_last: bool) -> AsyncMock:
    ctx = rz.ThreadContext(
        thread_id="t1", last_message_id="m9", last_message_at=None,
        our_side_last=our_side_last, has_external=True, thread_text="thread",
        messages=THREAD)
    monkeypatch.setattr(rz, "build_thread_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(rz, "_status_corrections_block", AsyncMock(return_value=""))
    monkeypatch.setattr(rz, "_load_assistant_about", AsyncMock(return_value=("", "")))
    upsert = AsyncMock()
    monkeypatch.setattr(rz, "_upsert_thread_status", upsert)
    return upsert


async def test_recompute_writes_nothing_with_no_decision(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=on")
    fake = _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
    llm = _llm_tripwire_all(monkeypatch)
    upsert = _status_env(monkeypatch, our_side_last=True)
    out = await rz.recompute_thread_status(_owner_db(), ACC, "t1", trigger="backfill")
    assert out is None
    upsert.assert_not_awaited()
    assert llm == [] and fake.calls[0]["member"] == OWNER


async def test_a_decided_status_never_gets_the_auto_tag(monkeypatch, tenant) -> None:
    """Item 7, and the owner as a proven member (item 5)."""
    _modes(monkeypatch, "email.thread_status=on")
    fake = _fake(monkeypatch, choices={"status": "AWAITING_REPLY"})
    upsert = _status_env(monkeypatch, our_side_last=True)
    out = await rz.recompute_thread_status(_owner_db(), ACC, "t1", trigger="outbound")
    assert out == ("AWAITING", "Awaiting Reply")
    assert upsert.await_args.args[6] == "Replied — AWAITING_REPLY"
    assert fake.calls[0]["member"] == OWNER
    assert fake.calls[0]["member_proven"] is True


async def test_off_still_tags_a_fallback_with_auto(monkeypatch, tenant) -> None:
    """The contrast: outside `on` the old fallback keeps its `· auto` tag, so
    the test above proves a change of `on` and not a dead tag."""
    llm = _llm_tripwire_all(monkeypatch)
    upsert = _status_env(monkeypatch, our_side_last=True)
    await rz.recompute_thread_status(_owner_db(), ACC, "t1", trigger="outbound")
    assert upsert.await_args.args[6].endswith("· auto")
    assert len(llm) == 2  # the configured tier, then the escalation


async def test_mark_thread_replied_leaves_the_labels_with_no_decision(
    monkeypatch, tenant,
) -> None:
    _modes(monkeypatch, "email.thread_status=on")
    _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
    llm = _llm_tripwire_all(monkeypatch)
    upsert = _status_env(monkeypatch, our_side_last=True)
    reconcile = AsyncMock()
    instantiate = MagicMock()
    monkeypatch.setattr(rz, "_reconcile_thread_labels", reconcile)
    monkeypatch.setattr(rz, "_instantiate_provider", instantiate)
    db = _owner_db()

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(rz, "_tenant_session", session)
    await rz._mark_thread_replied(ACC, "t1", sent_body="Thanks", sent_subject="Re")
    upsert.assert_not_awaited()
    reconcile.assert_not_awaited()
    instantiate.assert_not_called()
    assert llm == []


#: The enabled "Needs Reply" rule, as `_enabled_conversation_rules` keys it.
#: It has instructions, so the rule match also takes it as a candidate.
NEEDS_REPLY = {"id": "v0", "name": "Needs Reply", "system_type": "REPLY",
               "instructions": "Emails I need to reply to.", "actions": []}


def _resolver_env(
    monkeypatch, rules: dict[str, Any] | None = None, *, conversation: bool = True,
) -> AsyncMock:
    """The `on` resolver with its reads patched. `rules` is the enabled
    conversation rule of each status key (fix round 3). Returns the restore
    mock, which runs only when a determined rule becomes the live match."""
    _status_env(monkeypatch, our_side_last=False)
    monkeypatch.setattr(rz, "_thread_is_conversation",
                        AsyncMock(return_value=conversation))
    restore = AsyncMock()
    monkeypatch.setattr(rz, "_restore_conversation_messages", restore)
    monkeypatch.setattr(rz, "_enabled_conversation_rules", AsyncMock(
        return_value=dict({"REPLY": NEEDS_REPLY} if rules is None else rules)))
    return restore


async def test_the_resolver_decides_the_conversation_on_jev(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=on")
    fake = _fake(monkeypatch, choices={"status": "REPLY"})
    llm = _llm_tripwire_all(monkeypatch)
    restore = _resolver_env(monkeypatch)
    receipt = {"rule": {"id": "c", "name": "Receipt"}, "reason": "r", "source": "ai"}
    row = SimpleNamespace(id=MID, thread_id="t1")
    out = await rz.resolve_conversation_status_matches(_owner_db(), ACC, row, [receipt])
    assert out[0]["source"] == "thread_status"
    assert out[0]["reason"] == "Thread status: REPLY"
    assert out[0]["rule"] is NEEDS_REPLY
    assert out[1]["suppressed"] == "conversation"
    restore.assert_awaited_once()
    assert fake.calls[0]["member"] == OWNER and llm == []


async def test_the_resolver_raises_with_no_decision_and_writes_nothing(
    monkeypatch, tenant,
) -> None:
    """Inside `classify_matches` a missing status passes the broad handler
    of the resolver, so the runner skips the row (item 6)."""
    _modes(monkeypatch, "email.thread_status=on")
    _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
    restore = _resolver_env(monkeypatch)
    row = SimpleNamespace(id=MID, thread_id="t1")
    with pytest.raises(eng.DecisionUnavailable):
        await rz.resolve_conversation_status_matches(_owner_db(), ACC, row, [])
    restore.assert_not_awaited()


async def test_the_cold_gate_names_the_owner_and_writes_only_above_the_bar(
    monkeypatch, tenant,
) -> None:
    _modes(monkeypatch, "email.cold_check=on")
    fake = _fake(monkeypatch, by_key={"cold": 0.95})
    provider = MagicMock(move_to_folder=AsyncMock(), set_labels=AsyncMock())
    db = _site_db()
    await snd._maybe_block_cold(db, provider, ACC, MID, "pm-1", EMAIL, "ARCHIVE")
    assert fake.calls[0]["member"] == OWNER and fake.calls[0]["member_proven"] is True
    assert any(s.startswith("INSERT INTO email_cold_senders") for s in db.sql)
    provider.move_to_folder.assert_awaited_once()


@pytest.mark.parametrize("answer", ["below", "none"])
async def test_the_cold_gate_blocks_nothing_below_the_bar_or_with_no_answer(
    monkeypatch, tenant, answer,
) -> None:
    _modes(monkeypatch, "email.cold_check=on")
    if answer == "below":
        _fake(monkeypatch, by_key={"cold": 0.49})
    else:
        _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
    provider = MagicMock(move_to_folder=AsyncMock(), set_labels=AsyncMock())
    db = _site_db()
    await snd._maybe_block_cold(db, provider, ACC, MID, "pm-1", EMAIL, "ARCHIVE")
    assert not any(s.startswith(("INSERT", "UPDATE")) for s in db.sql), db.sql
    provider.move_to_folder.assert_not_awaited()
    provider.set_labels.assert_not_awaited()


async def test_the_pin_names_the_owner(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.sender_pin=on")
    fake = _fake(monkeypatch, by_key={"always": 0.95})
    with job_member_scope("someone.else@acme-on.example", app="email"):
        assert await _pin() is True
    assert fake.calls[0]["member"] == OWNER and fake.calls[0]["member_proven"] is True


# ═══ Fix round 3 (review, 2026-10-03) ═══════════════════════════════════════
#
# 1. A cold blocker that ARCHIVES needs 0.7. One that labels keeps 0.5.
# 2. A thread status whose conversation rule moves mail needs 0.7.
# 3. The status is asked BEFORE the rule match when it is sure to be asked,
#    so a missing status costs no rule match.
# 4. With no enabled conversation rule, no status is asked.
# 5. The sent rows of the Reply Zero backfill keep the new-mail floor (R8,
#    in `TestTheReplyZeroBackfillOnJev`).


@pytest.mark.parametrize(("blocker", "p", "cold"), [
    ("ARCHIVE", 0.5, False), ("ARCHIVE", 0.69, False), ("ARCHIVE", 0.7, True),
    ("LABEL", 0.5, True), ("LABEL", 0.69, True),
])
async def test_an_archiving_cold_blocker_needs_the_move_bar(
    monkeypatch, tenant, blocker, p, cold,
) -> None:
    """Fix 1: an archive moves mail, so it needs the 0.7 of a rule that
    moves mail. A blocker that only labels keeps the 0.5 of the table."""
    _modes(monkeypatch, "email.cold_check=on")
    _fake(monkeypatch, by_key={"cold": p})
    provider = MagicMock(move_to_folder=AsyncMock(), set_labels=AsyncMock())
    db = _site_db()
    with structlog.testing.capture_logs() as caps:
        await snd._maybe_block_cold(db, provider, ACC, MID, "pm-1", EMAIL, blocker)
    wrote = any(s.startswith("INSERT INTO email_cold_senders") for s in db.sql)
    assert wrote is cold
    assert provider.move_to_folder.await_count == (1 if cold and blocker == "ARCHIVE" else 0)
    (rec,) = _records(caps, "decide.decided")
    assert (rec["cold"], rec["threshold"]) == (cold, 0.7 if blocker == "ARCHIVE" else 0.5)


@pytest.mark.parametrize(("blocker", "new"), [("ARCHIVE", False), ("LABEL", True)])
async def test_the_cold_shadow_line_uses_the_bar_of_the_blocker(
    monkeypatch, tenant, blocker, new,
) -> None:
    """Fix 1 in `shadow`: the line shows what `on` would do with this blocker."""
    _modes(monkeypatch, "email.cold_check=shadow")
    _fake(monkeypatch, by_key={"cold": 0.6})
    _llm_tripwire_all(monkeypatch)
    with structlog.testing.capture_logs() as caps:
        assert await _cold(blocker) == (False, "")  # the old answer acts
    (rec,) = _records(caps, "decide.shadow")
    assert rec["new"] is new


#: "Done" as a member can set it up: a label and an ARCHIVE.
DONE_ARCHIVES = {"id": "v2", "name": "Done", "system_type": "DONE",
                 "actions": [{"type": "LABEL"}, {"type": "ARCHIVE"}]}
DONE_LABELS = {"id": "v2", "name": "Done", "system_type": "DONE",
               "actions": [{"type": "LABEL"}]}
RECEIPT_MATCH = {"rule": {"id": "c", "name": "Receipt"}, "reason": "r", "source": "ai"}


class _StatusAt(FakeDecide):
    """Answers the `status` choice with `choice` at probability `p`. The
    other options share the rest. Every other question answers as
    FakeDecide does."""

    def __init__(self, choice: str, p: float, **kw) -> None:
        super().__init__(**kw)
        self.choice = choice
        self.p_choice = p

    async def __call__(self, state, questions, **kwargs):
        decision = await super().__call__(state, questions, **kwargs)
        if "status" not in questions:
            return decision
        keys = list(questions["status"].criteria)
        rest = (1.0 - self.p_choice) / max(1, len(keys) - 1)
        answers = dict(decision.answers)
        answers["status"] = decide_mod.ChoiceAnswer(
            choice=self.choice, confidence=self.p_choice,
            probabilities=MappingProxyType(
                {k: (self.p_choice if k == self.choice else rest) for k in keys}))
        return decide_mod.Decision(
            answers=MappingProxyType(answers), request_id=decision.request_id)


@pytest.mark.parametrize(("rule", "p", "applies"), [
    (DONE_ARCHIVES, 0.6, False), (DONE_ARCHIVES, 0.69, False),
    (DONE_ARCHIVES, 0.7, True), (DONE_ARCHIVES, 0.9, True),
    (DONE_LABELS, 0.4, True),
])
async def test_a_status_whose_rule_moves_mail_needs_the_move_bar(
    monkeypatch, tenant, rule, p, applies,
) -> None:
    """Fix 2: the status selects the rule, and the runner runs its actions.
    Under 0.7 a moving rule does not run, and the per-message pick stands,
    as for a status with no enabled rule. A label-only rule keeps the
    plurality of the choice."""
    _modes(monkeypatch, "email.thread_status=on")
    monkeypatch.setattr(decide_mod, "decide", _StatusAt("DONE", p))
    restore = _resolver_env(monkeypatch, {"DONE": rule})
    row = SimpleNamespace(id=MID, thread_id="t1")
    with structlog.testing.capture_logs() as caps:
        out = await rz.resolve_conversation_status_matches(
            _owner_db(), ACC, row, [RECEIPT_MATCH])
    (dec,) = _records(caps, "decide.decided")
    assert dec["move_bar_met"] is applies
    if applies:
        assert out[0]["rule"] is rule and out[0]["source"] == "thread_status"
        restore.assert_awaited_once()
    else:
        assert out == [RECEIPT_MATCH]
        restore.assert_not_awaited()
        (rec,) = _records(caps, "email.thread_status_under_move_bar")
        assert (rec["status"], rec["message_id"]) == ("DONE", MID)


def _classify_env(
    monkeypatch, conv: dict[str, Any], *, conversation: bool,
) -> AsyncMock:
    """`classify_matches` in `on`: the rule match over `_RULES` plus the
    rules of `conv`, and the resolver over `conv`."""
    db = _engine_env(monkeypatch, [*_RULES, *conv.values()])
    _resolver_env(monkeypatch, conv, conversation=conversation)
    return db


def _asked(fake: FakeDecide) -> list[str]:
    """Which question each decide call asked, in order: the status or the
    rule match."""
    return ["status" if "status" in c["questions"] else "rule_match" for c in fake.calls]


async def test_the_status_is_asked_before_the_rule_match(monkeypatch, tenant) -> None:
    """Fix 3: the thread is already a conversation, so the status is sure to
    be asked. It goes first."""
    _modes(monkeypatch, ALL_ON)
    fake = _fake(monkeypatch, by_key={"r0": 0.9}, choices={"status": "REPLY"})
    llm = _llm_tripwire_all(monkeypatch)
    db = _classify_env(monkeypatch, {"REPLY": NEEDS_REPLY}, conversation=True)
    row = SimpleNamespace(id=MID, thread_id="t1")
    out = await eng.classify_matches(db, ACC, row, EMAIL, resolve=True)
    assert _asked(fake) == ["status", "rule_match"]
    assert out[0]["rule"] is NEEDS_REPLY and out[0]["source"] == "thread_status"
    assert out[1]["suppressed"] == "conversation"
    assert llm == []


@pytest.mark.parametrize("multi", [False, True])
async def test_a_missing_status_costs_no_rule_match(monkeypatch, tenant, multi) -> None:
    """Fix 3: the status fails first, so the email is undecided (D-EM-8)
    and no rule match is paid for it. The next cycle asks again."""
    _modes(monkeypatch, ALL_ON)
    fake = _FailOn("status", decide_mod.DecideUnavailable("HTTP 503"), p=0.9)
    monkeypatch.setattr(decide_mod, "decide", fake)
    llm = _llm_tripwire_all(monkeypatch)
    db = _classify_env(monkeypatch, {"REPLY": NEEDS_REPLY}, conversation=True)
    row = SimpleNamespace(id=MID, thread_id="t1")
    with pytest.raises(eng.DecisionUnavailable):
        await eng.classify_matches(db, ACC, row, EMAIL, multi_rule=multi, resolve=True)
    assert _asked(fake) == ["status"]
    assert llm == []


@pytest.mark.parametrize(("conv_pick", "asked"), [
    ("r2", ["rule_match", "status"]), ("none", ["rule_match"]),
])
async def test_a_new_thread_asks_the_status_only_after_a_conversation_match(
    monkeypatch, tenant, conv_pick, asked,
) -> None:
    """Fix 3, the other case: a thread that is not yet a conversation needs
    a status only when the rule match picks a conversation rule. So the
    status waits for the match, and an email that matches no conversation
    rule costs no status call."""
    _modes(monkeypatch, ALL_ON)
    fake = _fake(monkeypatch, choices={"conv": conv_pick, "status": "REPLY"})
    db = _classify_env(monkeypatch, {"REPLY": NEEDS_REPLY}, conversation=False)
    row = SimpleNamespace(id=MID, thread_id="t1")
    await eng.classify_matches(db, ACC, row, EMAIL, resolve=True)
    assert _asked(fake) == asked


async def test_no_enabled_conversation_rule_means_no_status_call(
    monkeypatch, tenant,
) -> None:
    """Fix 4: with no enabled conversation rule, no status can select a rule,
    so no status is asked, even for a thread that is a conversation."""
    _modes(monkeypatch, ALL_ON)
    fake = _fake(monkeypatch, by_key={"r0": 0.9, "r1": 0.1})
    db = _classify_env(monkeypatch, {}, conversation=True)
    row = SimpleNamespace(id=MID, thread_id="t1")
    out = await eng.classify_matches(db, ACC, row, EMAIL, resolve=True)
    assert _asked(fake) == ["rule_match"]
    assert [m["rule"]["id"] for m in out] == ["1"]


async def test_the_resolver_alone_asks_nothing_with_no_conversation_rule(
    monkeypatch, tenant,
) -> None:
    """Fix 4 at the resolver, for a caller with no step before the match."""
    _modes(monkeypatch, "email.thread_status=on")
    fake = _fake(monkeypatch, choices={"status": "REPLY"})
    _resolver_env(monkeypatch, {})
    row = SimpleNamespace(id=MID, thread_id="t1")
    out = await rz.resolve_conversation_status_matches(_owner_db(), ACC, row, [RECEIPT_MATCH])
    assert out == [RECEIPT_MATCH] and fake.calls == []


async def test_the_step_before_the_match_reads_nothing_outside_on(monkeypatch, tenant) -> None:
    """Off and shadow keep the order of before: the step reads nothing."""
    for modes in ("", "email.thread_status=shadow,email.rule_match=on"):
        _modes(monkeypatch, modes)
        db = AsyncMock()
        row = SimpleNamespace(id=MID, thread_id="t1")
        assert await rz.status_before_match(db, ACC, row) is None
        db.execute.assert_not_awaited()


# ── Item 2 (fix round 2): one cool-down for the organization ───────────────


async def test_a_402_on_one_feature_cools_the_others(monkeypatch, tenant) -> None:
    """The credit belongs to the organization, so a 402 from the cold check
    stops the status and the pin calls too, with no Router call."""
    _modes(monkeypatch, ALL_ON)
    fake = _fake(monkeypatch, raises=decide_mod.DecideUnavailable(
        "insufficient_credits", status=402))
    assert await _cold() == (False, "")
    with structlog.testing.capture_logs() as caps:
        with pytest.raises(eng.DecisionUnavailable):
            await _status()
        assert await _pin() is False
    assert len(fake.calls) == 1, "a call ran inside the cool-down"
    assert [r["decide_reason"] for r in _records(caps, "decide.unavailable")] \
        == ["cooldown", "cooldown"]


# ── Item 9: the decide.decided line, keys only ─────────────────────────────


@pytest.mark.parametrize(("feature", "fields"), [
    ("email.thread_status", {"answer": "REPLY", "options": 4}),
    ("email.cold_check", {"p_cold": 0.8, "cold": True}),
    ("email.sender_pin", {"p_always": 0.95, "pin": True}),
])
async def test_the_decided_line_holds_keys_only(monkeypatch, tenant, feature, fields) -> None:
    _modes(monkeypatch, f"{feature}=on")
    _fake(monkeypatch, by_key={"cold": 0.8, "always": 0.95}, choices={"status": "REPLY"})
    with structlog.testing.capture_logs() as caps:
        await SITE_CALLS[feature]()
    (rec,) = _records(caps, "decide.decided")
    assert (rec["decide_feature"], rec["account_id"], rec["message_id"]) == (feature, ACC, MID)
    assert rec["request_ids"] == ["req-1"] and isinstance(rec["latency_ms"], int)
    assert {k: rec[k] for k in fields} == fields
    text_ = repr([c for c in caps if c["event"].startswith("decide.")])
    for secret in (*SECRETS, SENDER, PIN_RULE["name"]):
        assert secret not in text_, secret


# ── Item 9: the startup check ──────────────────────────────────────────────


def _wiring(monkeypatch, *, enabled: bool, wired: bool) -> None:
    import acb_auth.console_resolve as resolve_mod

    monkeypatch.setenv("DECIDE_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()
    monkeypatch.setattr(resolve_mod, "router_is_wired", lambda: wired)


@pytest.mark.parametrize(("enabled", "wired"), [(False, True), (True, False), (False, False)])
def test_the_startup_check_logs_once_when_on_and_not_wired(monkeypatch, enabled, wired) -> None:
    _modes(monkeypatch, "email.thread_status=on,email.cold_check=shadow")
    _wiring(monkeypatch, enabled=enabled, wired=wired)
    with structlog.testing.capture_logs() as caps:
        hooks_mod.register_email_post_sync_hooks()
    (rec,) = _records(caps, "email.decide_not_wired")
    assert rec["log_level"] == "error"
    assert rec["decide_features"] == ["email.thread_status"]
    assert (rec["decide_enabled"], rec["router_wired"]) == (enabled, wired)


@pytest.mark.parametrize(("modes", "orgs", "enabled", "wired"), [
    (ALL_ON, "*", True, True),  # wired
    ("email.thread_status=shadow", "*", False, False),  # nothing is `on`
    (ALL_ON, "", False, False),  # no organization may run `on`
])
def test_the_startup_check_is_quiet_otherwise(monkeypatch, modes, orgs, enabled, wired) -> None:
    _modes(monkeypatch, modes, orgs)
    _wiring(monkeypatch, enabled=enabled, wired=wired)
    with structlog.testing.capture_logs() as caps:
        hooks_mod.register_email_post_sync_hooks()
    assert _records(caps, "email.decide_not_wired") == []


def test_a_broken_wiring_read_is_not_wired(monkeypatch) -> None:
    import acb_auth.console_resolve as resolve_mod

    _modes(monkeypatch, ALL_ON)
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    get_settings.cache_clear()

    def broken() -> bool:
        raise RuntimeError("no settings")

    monkeypatch.setattr(resolve_mod, "router_is_wired", broken)
    with structlog.testing.capture_logs() as caps:
        hooks_mod.check_decide_wiring()
    (rec,) = _records(caps, "email.decide_not_wired")
    assert rec["router_wired"] is False


# ── R8: each site on a real database, as the non-owner role under FORCE RLS ─


class _ArchivingProvider(_FakeProvider):
    async def move_to_folder(self, pmid, folder):
        self.calls.append(f"move_to_folder:{folder}")


def _status_rows(admin, account_id: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT thread_id, status, reason FROM email_thread_status "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).mappings().all())


def _conversation(p, rule: str = "Needs Reply") -> tuple[str, str, str, str]:
    """A mailbox of org B with one rule (the "Needs Reply" conversation rule
    by default), and a thread where the owner wrote first and the other
    party answered after the rule. Returns (account, owner, thread, the new
    message)."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    now = datetime.now(UTC)
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name=rule,
               instructions="Emails I need to respond to.",
               created_at=now - timedelta(hours=1))
    tid = f"t-conv-{acc}"
    _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                  thread_id=tid, received_at=now - timedelta(minutes=30))
    new = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid,
                        received_at=now - timedelta(minutes=5))
    return acc, owner, tid, new


@_DB_GATE
class TestTheThreadStatusOnJev:

    async def test_the_runner_resolves_a_conversation_on_jev(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, tid, new = _conversation(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, choices={"status": "REPLY"})
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        call = _call_for(fake, "status")
        assert call["member"] == owner and call["member_proven"] is True
        # Fix round 3: the thread is a conversation, so the status goes first.
        assert _asked(fake) == ["status", "rule_match"]
        assert llm == []
        assert _status_rows(p.admin_engine, acc) == [
            {"thread_id": tid, "status": "NEEDS_REPLY", "reason": "Thread status: REPLY"}]
        assert _stamps(p.admin_engine, acc)[new] is not None
        assert [(r["mid"], r["status"], r["rule_name"], r["match_source"])
                for r in _logged(p.admin_engine, acc)] == [
            (new, "APPLIED", "Needs Reply", "thread_status")]

    async def test_no_status_skips_the_row_and_writes_nothing(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        acc, _owner, _tid, new = _conversation(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _FailOn("status", decide_mod.DecideUnavailable("HTTP 503"))
        monkeypatch.setattr(decide_mod, "decide", fake)
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        with structlog.testing.capture_logs() as caps:
            async with _as_app(p, p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert llm == []
        # Fix round 3: the missing status came first, so no rule match was paid.
        assert _asked(fake) == ["status"]
        assert _status_rows(p.admin_engine, acc) == []
        assert _stamps(p.admin_engine, acc)[new] is None
        assert _logged(p.admin_engine, acc) == []
        assert [r["message_id"] for r in _records(caps, "email.classify_unavailable_skip")] \
            == [new]

    async def test_no_conversation_rule_means_no_status_call(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix 4 on the runner: the mailbox has no conversation rule, so the
        conversation thread costs no status call. The rule match decides."""
        p = promoted
        acc, _owner, _tid, new = _conversation(p, rule="Receipt")
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, p=0.92)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert _asked(fake) == ["rule_match"]
        assert _stamps(p.admin_engine, acc)[new] is not None
        assert [(r["mid"], r["status"], r["rule_name"]) for r in _logged(p.admin_engine, acc)] \
            == [(new, "APPLIED", "Receipt")]

    def _auto_row(self, p) -> tuple[str, str, str]:
        """A sent thread whose stored status is a guess (`· auto`). The sent
        message came after the first enabled rule, so it is over the
        new-mail floor (fix round 3)."""
        owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
                   instructions="Receipts and invoices.",
                   created_at=datetime.now(UTC) - timedelta(hours=1))
        tid = f"t-auto-{acc}"
        sent = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                             thread_id=tid)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, status, "
                "last_message_id, last_message_at, reason, organization_id) VALUES "
                "(CAST(:a AS uuid), :t, 'AWAITING', CAST(:m AS uuid), now(), :r, "
                "CAST(:o AS uuid))"),
                {"a": acc, "t": tid, "m": sent, "r": "Replied — AWAITING_REPLY · auto",
                 "o": p.org_b})
        return acc, owner, tid

    async def test_an_auto_row_gets_one_more_check_and_then_none(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Item 7: the backfill checks the guessed row once more on Jev, as
        the owner, and the decided row carries no `· auto`, so the next
        cycle leaves it."""
        p = promoted
        acc, owner, tid = self._auto_row(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, choices={"status": "DONE"})
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        for _ in range(2):
            async with _as_app(p, p.org_b):
                await rz._maybe_classify_threads(acc)
        assert len(fake.calls) == 1, "a decided row was asked again"
        assert fake.calls[0]["member"] == owner and llm == []
        assert _status_rows(p.admin_engine, acc) == [
            {"thread_id": tid, "status": "DONE", "reason": "Replied — DONE"}]

    async def test_with_no_answer_the_auto_row_stays_and_is_asked_again(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        acc, _owner, tid = self._auto_row(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        for _ in range(2):
            async with _as_app(p, p.org_b):
                await rz._maybe_classify_threads(acc)
        assert len(fake.calls) == 2 and llm == []
        assert _status_rows(p.admin_engine, acc) == [
            {"thread_id": tid, "status": "AWAITING",
             "reason": "Replied — AWAITING_REPLY · auto"}]


def _cold_mailbox(p) -> tuple[str, str, str]:
    owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
               instructions="Receipts and invoices.",
               created_at=datetime.now(UTC) - timedelta(hours=1))
    _seed_settings(p.admin_engine, org=p.org_b, account_id=acc,
                   cold_email_blocker="ARCHIVE")
    new = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                        thread_id=f"t-cold-{acc}",
                        received_at=datetime.now(UTC) - timedelta(minutes=5))
    return acc, owner, new


def _cold_rows(admin, account_id: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT from_email, status, reason FROM email_cold_senders "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).mappings().all())


def _folder(admin, message_id: str) -> str:
    with admin.connect() as c:
        return c.execute(text("SELECT folder FROM email_messages WHERE id = CAST(:m AS uuid)"),
                         {"m": message_id}).scalar_one()


@_DB_GATE
class TestTheColdCheckOnJev:

    async def test_a_cold_email_above_the_bar_is_archived(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, new = _cold_mailbox(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, by_key={"r0": 0.1, "cold": 0.95})
        llm = _llm_tripwire_all(monkeypatch)
        provider = _ArchivingProvider()
        _patch_providers(monkeypatch, provider)
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        call = _call_for(fake, "cold")
        assert call["member"] == owner and call["member_proven"] is True
        assert llm == []
        assert _cold_rows(p.admin_engine, acc) == [{
            "from_email": "s@sender.test", "status": "AI_LABELED_COLD",
            "reason": "Cold outreach by AI (probability 0.95)."}]
        assert _folder(p.admin_engine, new) == "archive"
        assert "move_to_folder:archive" in provider.calls
        assert _stamps(p.admin_engine, acc)[new] is not None
        assert sorted((r["status"], r["rule_name"] or "") for r in _logged(p.admin_engine, acc)) \
            == [("APPLIED", "Cold Email Blocker"), ("SKIPPED", "")]

    @pytest.mark.parametrize("answer", ["below", "under_the_move_bar", "none"])
    async def test_below_the_bar_or_with_no_answer_nothing_is_blocked(
        self, promoted, app_engine, monkeypatch, answer,  # noqa: F811
    ):
        """No cold row, no label, no archive. The rule outcome ("No rule
        matched") stands, so the runner stamps the message (item 6). The
        blocker of this mailbox ARCHIVES, so 0.6 is under its bar too (fix
        round 3)."""
        p = promoted
        acc, _owner, new = _cold_mailbox(p)
        _modes(monkeypatch, ALL_ON, "*")
        if answer == "below":
            _fake(monkeypatch, by_key={"r0": 0.1, "cold": 0.49})
        elif answer == "under_the_move_bar":
            _fake(monkeypatch, by_key={"r0": 0.1, "cold": 0.6})
        else:
            monkeypatch.setattr(decide_mod, "decide", _FailOn(
                "cold", decide_mod.DecideUnavailable("HTTP 503"), by_key={"r0": 0.1}))
        llm = _llm_tripwire_all(monkeypatch)
        provider = _ArchivingProvider()
        _patch_providers(monkeypatch, provider)
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert llm == []
        assert _cold_rows(p.admin_engine, acc) == []
        assert _folder(p.admin_engine, new) == "inbox"
        assert not any(c.startswith("move_to_folder") for c in provider.calls)
        assert _stamps(p.admin_engine, acc)[new] is not None
        assert [(r["status"], r["rule_name"]) for r in _logged(p.admin_engine, acc)] \
            == [("SKIPPED", None)]


def _pin_mailbox(p) -> tuple[str, str, str, str]:
    """A Newsletter rule, four older messages of SENDER that it already
    matched (APPLIED), and one new message of SENDER. The run then reaches
    the pin question. Returns (account, owner, rule, new message)."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@decide-on.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    now = datetime.now(UTC)
    rid = _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Newsletter",
                     instructions="Newsletters.", created_at=now - timedelta(hours=1))
    for i in range(4):
        old = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, sender=SENDER,
                            thread_id=f"t-pin-{i}-{acc}",
                            received_at=now - timedelta(hours=2, minutes=i))
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_executed_rules (account_id, rule_id, rule_name, "
                "message_id, from_address, status, match_source, organization_id) "
                "VALUES (CAST(:a AS uuid), CAST(:r AS uuid), 'Newsletter', "
                "CAST(:m AS uuid), :f, 'APPLIED', 'ai', CAST(:o AS uuid))"),
                {"a": acc, "r": rid, "m": old, "f": SENDER, "o": p.org_b})
    new = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, sender=SENDER,
                        thread_id=f"t-pin-new-{acc}",
                        received_at=now - timedelta(minutes=5))
    return acc, owner, rid, new


def _patterns(admin, account_id: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT rule_id::text AS rule_id, pattern_type, value, source "
            "FROM email_rule_patterns WHERE account_id = CAST(:a AS uuid)"),
            {"a": account_id}).mappings().all())


@_DB_GATE
class TestTheSenderPinOnJev:

    async def test_a_pin_at_ninety_percent_is_written_as_the_owner(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, rid, new = _pin_mailbox(p)
        _modes(monkeypatch, ALL_ON, "*")
        fake = _fake(monkeypatch, by_key={"r0": 0.92, "always": 0.95})
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        call = _call_for(fake, "always")
        assert call["member"] == owner and call["member_proven"] is True
        assert llm == []
        assert _patterns(p.admin_engine, acc) == [
            {"rule_id": rid, "pattern_type": "FROM", "value": SENDER, "source": "AI"}]
        assert _stamps(p.admin_engine, acc)[new] is not None

    @pytest.mark.parametrize("answer", ["below", "none"])
    async def test_below_the_bar_or_with_no_answer_there_is_no_pin(
        self, promoted, app_engine, monkeypatch, answer,  # noqa: F811
    ):
        p = promoted
        acc, _owner, _rid, new = _pin_mailbox(p)
        _modes(monkeypatch, ALL_ON, "*")
        if answer == "below":
            _fake(monkeypatch, by_key={"r0": 0.92, "always": 0.89})
        else:
            monkeypatch.setattr(decide_mod, "decide", _FailOn(
                "always", decide_mod.DecideUnavailable("HTTP 503"), by_key={"r0": 0.92}))
        llm = _llm_tripwire_all(monkeypatch)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert llm == []
        assert _patterns(p.admin_engine, acc) == []
        # The rule still applied, and the message is stamped.
        assert _stamps(p.admin_engine, acc)[new] is not None
        assert ("APPLIED", "Newsletter") in [
            (r["status"], r["rule_name"]) for r in _logged(p.admin_engine, acc)
            if r["mid"] == new]
