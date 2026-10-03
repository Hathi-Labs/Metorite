"""EM-T8f-1 — ``POST /email/rules/copy`` copies the rules of one mailbox to
another mailbox of the same member.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item 1,
and D-EM-6, D-EM-18, D-EM-24 and D-EM-29 in §11.2.

R7 fences named here:

* ``email-rule-copy-owner``: a mailbox of another member, or of a second
  organization, gives 404 and writes no row. The same id twice gives 422.
* ``email-rule-copy-enabled-only``: each column and each action of an enabled
  rule matches its source. No disabled rule, pattern, guidance or learned
  pattern arrives. A column that no tuple of ``rule_copy`` names fails.
* ``email-rule-copy-names``: "X", then "X (copy)", then "X (copy 2)", with no
  error, also when another writer takes a name during the copy.
* ``email-rule-copy-floor``: the new-mail floor of the target is the time of
  the copy.
* ``email-rule-copy-drafting``: with ``draft_replies`` false or absent, no
  copied reply rule holds DRAFT_EMAIL. With it true, the rule keeps it. A rule
  that is not a reply rule keeps its DRAFT_EMAIL in each case.
* ``email-rule-copy-one-reply-rule`` (review round 1, P1): the target keeps
  one reply rule at most. A source reply rule is left out as
  ``reply_rule_exists`` when the target holds one, so the switch OFF stops
  every reply draft after a copy.
* ``email-rule-copy-forward-loop``: a rule with a FORWARD to an own address is
  left out and named.
* ``email-rule-copy-409``: a copy that cannot land answers 409 and writes
  nothing.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and reads the rows.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_rule_copy.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email import core
from gateway.routes.email.automation import rule_copy
from gateway.routes.email.automation import rules as rules_mod
from gateway.routes.email.automation.rules import NEW_MAIL_FLOOR_SQL
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_RULE_SKIP = set(rule_copy.RULE_COPY_WRITTEN) | set(rule_copy.RULE_COPY_SKIPPED)
_ACTION_SKIP = set(rule_copy.ACTION_COPY_WRITTEN) | set(rule_copy.ACTION_COPY_SKIPPED)


# ── hermetic ─────────────────────────────────────────────────────────────────


class TestTheNameRule:

    @pytest.mark.parametrize("taken,want", [
        (set(), "X"),
        ({"x"}, "X (copy)"),
        ({"x", "x (copy)"}, "X (copy 2)"),
        ({"x", "x (copy)", "x (copy 2)"}, "X (copy 3)"),
        ({"other"}, "X"),
    ])
    def test_the_first_free_name(self, taken, want):
        assert rule_copy.copy_name("X", taken) == want


class TestTheForwardRule:

    @pytest.mark.parametrize("action,own", [
        ({"type": "FORWARD", "to_address": "Box B <BOX-B@x.test>"}, True),
        ({"type": "forward", "to_address": "a@out.test; box-b@x.test"}, True),
        ({"type": "FORWARD", "to_address": "a@out.test", "cc_address": "box-b@x.test"}, True),
        ({"type": "FORWARD", "to_address": "a@out.test", "bcc_address": "Box-B@x.test"}, True),
        # A field that a strict parser refuses still names the address.
        ({"type": "FORWARD", "to_address": "Box B <box-b@x.test"}, True),
        ({"type": "FORWARD", "to_address": "a@out.test,, box-b@x.test"}, True),
        ({"type": "FORWARD", "to_address": "a@out.test"}, False),
        ({"type": "FORWARD", "to_address": None}, False),
        ({"type": "REPLY", "to_address": "box-b@x.test"}, False),
    ])
    def test_a_forward_to_an_own_address(self, action, own):
        rule = {"actions": [{"type": "LABEL", "label": "x"}, action]}
        assert rule_copy.forwards_to_own_address(rule, {"box-b@x.test"}) is own


class TestTheLeftOutReason:

    def _rule(self, **over: Any) -> dict[str, Any]:
        return {"name": "Needs Reply", "system_type": None, "enabled": True,
                "actions": [], **over}

    def test_each_reason(self):
        own = {"b@x.test"}
        reason = rule_copy._left_out_reason
        assert reason(self._rule(enabled=False), own, False) == rule_copy.LEFT_OUT_DISABLED
        assert reason(self._rule(actions=[{"type": "FORWARD", "to_address": "b@x.test"}]),
                      own, False) == rule_copy.LEFT_OUT_FORWARD_LOOP
        assert reason(self._rule(), own, True) == rule_copy.LEFT_OUT_REPLY_EXISTS
        assert reason(self._rule(name="X", system_type="REPLY"), own, True) == (
            rule_copy.LEFT_OUT_REPLY_EXISTS)
        assert reason(self._rule(), own, False) is None
        assert reason(self._rule(name="Vendors"), own, True) is None


class TestTheRoute:

    def test_the_route_is_registered(self):
        hits = [r for r in core.router.routes
                if getattr(r, "path", "") == "/email/rules/copy"]
        assert [sorted(r.methods) for r in hits] == [["POST"]]

    async def test_the_same_id_twice_is_422_before_any_session(self, monkeypatch):
        @asynccontextmanager
        async def _no_session(*_a: Any):
            raise AssertionError("the route opened a session")
            yield  # pragma: no cover

        monkeypatch.setattr(rule_copy, "_tenant_session", _no_session)
        same = uuid.uuid4()
        with pytest.raises(HTTPException) as err:
            await rule_copy.copy_rules(
                rule_copy.RuleCopyRequest(from_account_id=same, to_account_id=same),
                user=UserContext(email="m@x.test", role=UserRole.EMPLOYEE))
        assert err.value.status_code == 422


# ── R8 helpers ───────────────────────────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _account(admin, *, org: str, owner: str, address: str | None = None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": address or f"box-{uuid.uuid4().hex[:8]}@em-t8f.test",
             "o": org}).scalar_one())


#: A value other than the default in every column, so a column that the copy
#: drops shows up as a difference.
_RULE_VALUES: dict[str, Any] = {
    "instructions": "Invoices from vendors.", "automated": False,
    "run_on_threads": True, "conditional_operator": "OR",
    "from_pattern": "billing@vendor.test", "to_pattern": "ap@em-t8f.test",
    "subject_pattern": "Invoice", "body_pattern": "amount due",
    "category_filter_type": "INCLUDE", "category_filters": ["Receipt", "Vendor"],
    "system_type": None,
}


def _rule(admin, *, org: str, account: str, name: str, enabled: bool = True,
          age: str = "30 days", **over: Any) -> str:
    values = {**_RULE_VALUES, **over}
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, enabled, instructions, "
            "automated, run_on_threads, conditional_operator, from_pattern, "
            "to_pattern, subject_pattern, body_pattern, category_filter_type, "
            "category_filters, system_type, created_at, updated_at, "
            "organization_id) VALUES (CAST(:a AS uuid), :n, :e, :instructions, "
            ":automated, :run_on_threads, :conditional_operator, :from_pattern, "
            ":to_pattern, :subject_pattern, :body_pattern, :category_filter_type, "
            "CAST(:category_filters AS text[]), :system_type, "
            "now() - CAST(:age AS interval), now() - CAST(:age AS interval), "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account, "n": name, "e": enabled, "age": age, "o": org,
             **values}).scalar_one())


def _action(admin, *, org: str, rule: str, type_: str, **over: Any) -> None:
    values = {
        "label": None, "subject": None, "content": None, "to_address": None,
        "cc_address": None, "bcc_address": None, "url": None,
        "delay_minutes": None, "attachments": [], "label_ai": False,
        "content_manual": False, **over,
    }
    values["attachments"] = json.dumps(values["attachments"])
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, subject, content, "
            "to_address, cc_address, bcc_address, url, delay_minutes, "
            "attachments, label_ai, content_manual, organization_id) "
            "VALUES (CAST(:r AS uuid), :t, :label, :subject, :content, "
            ":to_address, :cc_address, :bcc_address, :url, :delay_minutes, "
            "CAST(:attachments AS jsonb), :label_ai, :content_manual, "
            "CAST(:o AS uuid))"),
            {"r": rule, "t": type_, "o": org, **values})


def _settings(admin, *, org: str, account: str, draft_replies: bool) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, draft_replies, "
            "writing_style, organization_id) VALUES (CAST(:a AS uuid), :d, "
            "'source voice', CAST(:o AS uuid))"),
            {"a": account, "d": draft_replies, "o": org})


def _rows(admin, sql: str, **params: Any) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings().all()]


def _rules_of(admin, account: str) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in _rows(
        admin, "SELECT * FROM email_rules WHERE account_id = CAST(:a AS uuid)",
        a=account)}


def _actions_of(admin, rule_id: Any) -> list[dict[str, Any]]:
    return _rows(admin, "SELECT * FROM email_actions WHERE rule_id = CAST(:r AS uuid) "
                        "ORDER BY created_at, ctid", r=str(rule_id))


def _drop(row: dict[str, Any], skip: set[str]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in skip}


def _purge(admin, owner: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": owner})


class _Member:
    """One member of organization B, and how to call the route as them."""

    def __init__(self, p: Any) -> None:
        self.p = p
        self.email = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        self.user = UserContext(email=self.email, role=UserRole.EMPLOYEE,
                                organization_id=p.org_b)

    def box(self, address: str | None = None, org: str | None = None) -> str:
        return _account(self.p.admin_engine, org=org or self.p.org_b,
                        owner=self.email, address=address)

    async def copy(self, src: str, dst: str, org: str | None = None):
        app_dsn = self.p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(org or self.p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                return await rule_copy.copy_rules(
                    rule_copy.RuleCopyRequest(
                        from_account_id=uuid.UUID(src), to_account_id=uuid.UUID(dst)),
                    user=self.user)
        finally:
            release_tenant(token)


@pytest.fixture()
def member(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _Member(promoted)
    try:
        yield m
    finally:
        _purge(promoted.admin_engine, m.email)


# ── R8: email-rule-copy-owner ────────────────────────────────────────────────


@_DB_GATE
class TestOnlyTheMembersOwnMailboxes:

    async def test_a_mailbox_of_another_member_gives_404_and_no_row(
        self, member, promoted,  # noqa: F811
    ):
        p = promoted
        mine = member.box()
        _rule(p.admin_engine, org=p.org_b, account=mine, name="Mine")
        stranger = f"stranger-{uuid.uuid4().hex[:8]}@em-t8f.test"
        theirs = _account(p.admin_engine, org=p.org_b, owner=stranger)
        _rule(p.admin_engine, org=p.org_b, account=theirs, name="Theirs")
        try:
            for src, dst in ((theirs, mine), (mine, theirs)):
                with pytest.raises(HTTPException) as err:
                    await member.copy(src, dst)
                assert err.value.status_code == 404
            assert set(_rules_of(p.admin_engine, mine)) == {"Mine"}
            assert set(_rules_of(p.admin_engine, theirs)) == {"Theirs"}
        finally:
            _purge(p.admin_engine, stranger)

    async def test_a_mailbox_of_a_second_organization_gives_404_and_no_row(
        self, member, promoted,  # noqa: F811
    ):
        p = promoted
        in_b = member.box()
        in_a = member.box(org=p.org_a)  # the same member email, in org A
        _rule(p.admin_engine, org=p.org_a, account=in_a, name="From org A")
        _rule(p.admin_engine, org=p.org_b, account=in_b, name="From org B")
        for src, dst in ((in_a, in_b), (in_b, in_a)):
            with pytest.raises(HTTPException) as err:
                await member.copy(src, dst)
            assert err.value.status_code == 404
        assert set(_rules_of(p.admin_engine, in_b)) == {"From org B"}
        assert set(_rules_of(p.admin_engine, in_a)) == {"From org A"}

    async def test_the_same_id_twice_gives_422(self, member, promoted):  # noqa: F811
        box = member.box()
        _rule(promoted.admin_engine, org=promoted.org_b, account=box, name="Only")
        with pytest.raises(HTTPException) as err:
            await member.copy(box, box)
        assert err.value.status_code == 422
        assert set(_rules_of(promoted.admin_engine, box)) == {"Only"}


# ── R8: email-rule-copy-enabled-only ─────────────────────────────────────────


@_DB_GATE
class TestTheCopyTakesEachColumnOfAnEnabledRule:

    def test_each_column_is_in_one_tuple(self, promoted):  # noqa: F811
        """A column that a later migration adds must join a tuple of
        ``rule_copy``, or this fails."""
        for table, tuples in (
            ("email_rules", (rule_copy.RULE_COPY_COLUMNS, rule_copy.RULE_COPY_WRITTEN,
                             rule_copy.RULE_COPY_SKIPPED)),
            ("email_actions", (rule_copy.ACTION_COPY_COLUMNS,
                               rule_copy.ACTION_COPY_WRITTEN,
                               rule_copy.ACTION_COPY_SKIPPED)),
        ):
            live = {r["column_name"] for r in _rows(
                promoted.admin_engine,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t", t=table)}
            named = [c for t in tuples for c in t]
            assert len(named) == len(set(named)), f"{table}: a column is in two tuples"
            assert live == set(named), (
                f"{table}: the catalog and rule_copy disagree: "
                f"only live {sorted(live - set(named))}, "
                f"only named {sorted(set(named) - live)}")

    async def test_each_column_and_each_action_matches_its_source(
        self, member, promoted,  # noqa: F811
    ):
        p = promoted
        src, dst = member.box(), member.box()
        vendor = _rule(p.admin_engine, org=p.org_b, account=src, name="Vendors")
        _action(p.admin_engine, org=p.org_b, rule=vendor, type_="LABEL",
                label="{{vendor}}", label_ai=True)
        _action(p.admin_engine, org=p.org_b, rule=vendor, type_="MOVE_FOLDER",
                label="Vendors")
        _action(p.admin_engine, org=p.org_b, rule=vendor, type_="REPLY",
                subject="Re: invoice", content="Thanks, received.",
                cc_address="ap@outside.test", bcc_address="log@outside.test",
                delay_minutes=15, content_manual=True,
                attachments=[{"path": "agent-data/terms.pdf", "name": "terms.pdf",
                              "artifact_id": None, "ai_selected": True}])
        _action(p.admin_engine, org=p.org_b, rule=vendor, type_="CALL_WEBHOOK",
                url="https://hooks.outside.test/in")
        _rule(p.admin_engine, org=p.org_b, account=src, name="Newsletters",
              system_type="NEWSLETTER", category_filters=[])
        off = _rule(p.admin_engine, org=p.org_b, account=src, name="Old rule",
                    enabled=False)
        _action(p.admin_engine, org=p.org_b, rule=off, type_="ARCHIVE")
        # The AI context of the source, which must stay there (D-EM-18).
        with p.admin_engine.begin() as c:
            for sql in (
                "INSERT INTO email_rule_patterns (account_id, rule_id, value, "
                "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), "
                "'billing@vendor.test', CAST(:o AS uuid))",
                "INSERT INTO email_rule_guidance (account_id, rule_id, guidance, "
                "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), "
                "'Vendors pay by card.', CAST(:o AS uuid))",
                "INSERT INTO email_learned_patterns (account_id, pattern, "
                "organization_id) VALUES (CAST(:a AS uuid), 'Sign as V.', "
                "CAST(:o AS uuid))",
            ):
                c.execute(text(sql), {"a": src, "r": vendor, "o": p.org_b})
        _settings(p.admin_engine, org=p.org_b, account=src, draft_replies=True)

        out = await member.copy(src, dst)

        assert sorted(out.copied) == ["Newsletters", "Vendors"]
        assert out.renamed == []
        assert [(x.name, x.reason) for x in out.left_out] == [
            ("Old rule", rule_copy.LEFT_OUT_DISABLED)]
        before, after = _rules_of(p.admin_engine, src), _rules_of(p.admin_engine, dst)
        assert set(after) == {"Newsletters", "Vendors"}, "a disabled rule arrived"
        for name, copy in after.items():
            source = before[name]
            assert _drop(copy, _RULE_SKIP) == _drop(source, _RULE_SKIP), name
            assert str(copy["account_id"]) == dst
            assert str(copy["organization_id"]) == p.org_b
            assert copy["id"] != source["id"]
            assert copy["created_at"] > source["created_at"]
            assert ([_drop(a, _ACTION_SKIP) for a in _actions_of(p.admin_engine, copy["id"])]
                    == [_drop(a, _ACTION_SKIP)
                        for a in _actions_of(p.admin_engine, source["id"])]), name
        assert len(_actions_of(p.admin_engine, after["Vendors"]["id"])) == 4
        for table in ("email_rule_patterns", "email_rule_guidance",
                      "email_learned_patterns", "email_assistant_settings"):
            assert _rows(p.admin_engine,
                         f"SELECT 1 FROM {table} WHERE account_id = CAST(:a AS uuid)",
                         a=dst) == [], f"{table} arrived in the target"


# ── R8: email-rule-copy-names ────────────────────────────────────────────────


@_DB_GATE
class TestANameThatTheTargetHolds:

    async def test_x_then_x_copy_then_x_copy_2(self, member, promoted):  # noqa: F811
        p = promoted
        src, dst = member.box(), member.box()
        _rule(p.admin_engine, org=p.org_b, account=src, name="X")
        first = await member.copy(src, dst)
        second = await member.copy(src, dst)
        third = await member.copy(src, dst)
        assert (first.copied, second.copied, third.copied) == (
            ["X"], ["X (copy)"], ["X (copy 2)"])
        assert first.renamed == []
        assert [(r.name, r.copied_as) for r in third.renamed] == [("X", "X (copy 2)")]
        assert set(_rules_of(p.admin_engine, dst)) == {"X", "X (copy)", "X (copy 2)"}

    async def test_a_name_that_another_writer_took_moves_to_the_next(
        self, member, promoted,  # noqa: F811
    ):
        """The read of the target names missed "X", as when a second writer
        adds it during the copy. ``ON CONFLICT`` moves the copy on."""
        p = promoted
        src, dst = member.box(), member.box()
        rid = _rule(p.admin_engine, org=p.org_b, account=src, name="X")
        _rule(p.admin_engine, org=p.org_b, account=dst, name="X")
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn), core._tenant_session() as db:
                name = await rule_copy._copy_one(
                    db, {"id": rid, "name": "X"}, src, dst, set(), keep_draft=True)
        finally:
            release_tenant(token)
        assert name == "X (copy)"
        assert set(_rules_of(p.admin_engine, dst)) == {"X", "X (copy)"}


# ── R8: email-rule-copy-floor ────────────────────────────────────────────────


@_DB_GATE
class TestTheFloorOfTheTarget:

    async def test_the_floor_is_the_time_of_the_copy(self, member, promoted):  # noqa: F811
        p = promoted
        src, dst = member.box(), member.box()
        for name in ("A", "B"):
            _rule(p.admin_engine, org=p.org_b, account=src, name=name, age="30 days")
        [(t0,)] = [tuple(r.values()) for r in _rows(p.admin_engine, "SELECT now() AS t")]
        await member.copy(src, dst)
        [(t1,)] = [tuple(r.values()) for r in _rows(p.admin_engine, "SELECT now() AS t")]
        [(floor,)] = [tuple(r.values()) for r in _rows(
            p.admin_engine, f"SELECT {NEW_MAIL_FLOOR_SQL} AS f", aid=uuid.UUID(dst))]
        [(source_floor,)] = [tuple(r.values()) for r in _rows(
            p.admin_engine, f"SELECT {NEW_MAIL_FLOOR_SQL} AS f", aid=uuid.UUID(src))]
        assert floor is not None
        assert t0 <= floor <= t1, "the floor of the target is not the time of the copy"
        assert floor > source_floor, "the floor moved back into the imported mail"


# ── R8: email-rule-copy-drafting ─────────────────────────────────────────────


@_DB_GATE
class TestDraftingFollowsTheTarget:

    @pytest.mark.parametrize("reply_name,reply_type", [
        ("Needs Reply", None), ("Answer these", "REPLY"),
    ], ids=["reply-by-name", "reply-by-system-type"])
    @pytest.mark.parametrize("setting,keeps", [
        (None, False), (False, False), (True, True),
    ], ids=["no-settings-row", "draft-replies-off", "draft-replies-on"])
    async def test_a_reply_rule_keeps_draft_email_only_when_the_target_drafts(
        self, member, promoted, setting, keeps, reply_name, reply_type,  # noqa: F811
    ):
        p = promoted
        src, dst = member.box(), member.box()
        _settings(p.admin_engine, org=p.org_b, account=src, draft_replies=True)
        if setting is not None:
            _settings(p.admin_engine, org=p.org_b, account=dst, draft_replies=setting)
        reply = _rule(p.admin_engine, org=p.org_b, account=src, name=reply_name,
                      system_type=reply_type)
        # A rule that the switch does not govern: the member put DRAFT_EMAIL
        # there, and it stays whatever the target's setting is.
        vendors = _rule(p.admin_engine, org=p.org_b, account=src, name="Vendors")
        for rid in (reply, vendors):
            _action(p.admin_engine, org=p.org_b, rule=rid, type_="LABEL", label="x")
            _action(p.admin_engine, org=p.org_b, rule=rid, type_="DRAFT_EMAIL")

        out = await member.copy(src, dst)

        assert sorted(out.copied) == sorted([reply_name, "Vendors"])
        after = _rules_of(p.admin_engine, dst)

        def types(name: str) -> list[str]:
            return [a["type"] for a in _actions_of(p.admin_engine, after[name]["id"])]

        assert types(reply_name) == (["LABEL", "DRAFT_EMAIL"] if keeps else ["LABEL"])
        assert types("Vendors") == ["LABEL", "DRAFT_EMAIL"], (
            "a rule that is not a reply rule lost its DRAFT_EMAIL")


# ── R8: email-rule-copy-one-reply-rule ───────────────────────────────────────


@_DB_GATE
class TestTheTargetKeepsOneReplyRule:
    """The "Auto draft replies" switch edits only the first reply rule. A second
    reply rule in the target would go on drafting while the switch shows OFF."""

    async def test_after_a_copy_the_switch_off_stops_every_reply_draft(
        self, member, promoted,  # noqa: F811
    ):
        """The case of review round 1 (P1): the target holds Needs Reply and
        drafts. Before the fix, "Needs Reply (copy)" arrived with DRAFT_EMAIL,
        and the switch could not turn it off."""
        p = promoted
        src, dst = member.box(), member.box()
        _settings(p.admin_engine, org=p.org_b, account=dst, draft_replies=True)
        for box in (src, dst):
            rid = _rule(p.admin_engine, org=p.org_b, account=box, name="Needs Reply")
            _action(p.admin_engine, org=p.org_b, rule=rid, type_="LABEL", label="x")
            _action(p.admin_engine, org=p.org_b, rule=rid, type_="DRAFT_EMAIL")

        out = await member.copy(src, dst)

        assert out.copied == []
        assert [(x.name, x.reason) for x in out.left_out] == [
            ("Needs Reply", rule_copy.LEFT_OUT_REPLY_EXISTS)]
        assert set(_rules_of(p.admin_engine, dst)) == {"Needs Reply"}
        # The member turns drafting OFF, as SettingsTab does.
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn), core._tenant_session() as db:
                await rules_mod.sync_draft_reply_action(db, dst, False)
        finally:
            release_tenant(token)
        drafting = _rows(p.admin_engine,
                         "SELECT r.name FROM email_rules r JOIN email_actions a "
                         "ON a.rule_id = r.id WHERE r.account_id = CAST(:a AS uuid) "
                         "AND a.type = 'DRAFT_EMAIL'", a=dst)
        assert drafting == [], f"a rule still drafts with the switch OFF: {drafting}"

    @pytest.mark.parametrize("held_name,held_type,enabled", [
        ("Reply", None, True), ("to reply", None, False), ("Inbox triage", "REPLY", True),
    ], ids=["legacy-name", "disabled-legacy-name", "by-system-type"])
    async def test_any_reply_rule_of_the_target_counts(
        self, member, promoted, held_name, held_type, enabled,  # noqa: F811
    ):
        p = promoted
        src, dst = member.box(), member.box()
        _rule(p.admin_engine, org=p.org_b, account=dst, name=held_name,
              system_type=held_type, enabled=enabled)
        _rule(p.admin_engine, org=p.org_b, account=src, name="Needs Reply")
        _rule(p.admin_engine, org=p.org_b, account=src, name="Vendors")

        out = await member.copy(src, dst)

        assert out.copied == ["Vendors"]
        assert [(x.name, x.reason) for x in out.left_out] == [
            ("Needs Reply", rule_copy.LEFT_OUT_REPLY_EXISTS)]

    async def test_a_second_reply_rule_of_the_source_is_left_out(
        self, member, promoted,  # noqa: F811
    ):
        p = promoted
        src, dst = member.box(), member.box()
        _rule(p.admin_engine, org=p.org_b, account=src, name="Answer these",
              system_type="REPLY")
        _rule(p.admin_engine, org=p.org_b, account=src, name="Needs Reply")

        out = await member.copy(src, dst)

        assert len(out.copied) == 1 and len(out.left_out) == 1
        assert out.left_out[0].reason == rule_copy.LEFT_OUT_REPLY_EXISTS
        assert set(out.copied) | {out.left_out[0].name} == {"Answer these", "Needs Reply"}
        assert set(_rules_of(p.admin_engine, dst)) == set(out.copied)


# ── R8: the 409 of a copy that cannot land ───────────────────────────────────


@_DB_GATE
class TestACopyThatCannotLand:

    async def test_a_source_rule_that_left_gives_409_and_writes_nothing(
        self, member, promoted,  # noqa: F811
    ):
        """The INSERT finds no source row on each try, as when the rule left
        during the copy. After ``_MAX_NAME_TRIES`` tries the copy answers 409,
        and it never answers a name."""
        p = promoted
        src, dst = member.box(), member.box()
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn), core._tenant_session() as db:
                with pytest.raises(HTTPException) as err:
                    await rule_copy._copy_one(
                        db, {"id": str(uuid.uuid4()), "name": "Ghost"},
                        src, dst, set(), keep_draft=True)
        finally:
            release_tenant(token)
        assert err.value.status_code == 409
        assert _rules_of(p.admin_engine, dst) == {}


# ── R8: email-rule-copy-forward-loop ─────────────────────────────────────────


@_DB_GATE
class TestAForwardToAnOwnAddress:

    async def test_the_rule_is_left_out_and_named(self, member, promoted):  # noqa: F811
        p = promoted
        tag = uuid.uuid4().hex[:8]
        src = member.box(f"box-a-{tag}@em-t8f.test")
        dst = member.box(f"box-b-{tag}@em-t8f.test")
        member.box(f"box-c-{tag}@em-t8f.test")
        loop_to = _rule(p.admin_engine, org=p.org_b, account=src, name="Loop to B")
        _action(p.admin_engine, org=p.org_b, rule=loop_to, type_="LABEL", label="x")
        _action(p.admin_engine, org=p.org_b, rule=loop_to, type_="FORWARD",
                to_address=f"Box B <BOX-B-{tag}@EM-T8F.TEST>")
        loop_cc = _rule(p.admin_engine, org=p.org_b, account=src, name="Loop by Cc")
        _action(p.admin_engine, org=p.org_b, rule=loop_cc, type_="FORWARD",
                to_address="partner@outside.test",
                cc_address=f"box-c-{tag}@em-t8f.test")
        out_rule = _rule(p.admin_engine, org=p.org_b, account=src, name="To partner")
        _action(p.admin_engine, org=p.org_b, rule=out_rule, type_="FORWARD",
                to_address="partner@outside.test")

        out = await member.copy(src, dst)

        assert out.copied == ["To partner"]
        assert sorted((x.name, x.reason) for x in out.left_out) == [
            ("Loop by Cc", rule_copy.LEFT_OUT_FORWARD_LOOP),
            ("Loop to B", rule_copy.LEFT_OUT_FORWARD_LOOP),
        ]
        after = _rules_of(p.admin_engine, dst)
        assert set(after) == {"To partner"}
        [forward] = _actions_of(p.admin_engine, after["To partner"]["id"])
        assert (forward["type"], forward["to_address"]) == (
            "FORWARD", "partner@outside.test")
