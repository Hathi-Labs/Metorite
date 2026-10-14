"""The proposer of field rules (WS-53 CRM-Q7b, ``crm_platform.md`` §18.7).

``POST /crm/field-rules/suggest`` asks the model for a column for each field of
one entity that goes to ``custom``. The answer becomes ``proposed`` rows of
``crm_field_rules``, and an admin approves or rejects each one.

One test for each "Done when" item of §18.7 CRM-Q7b, on a real Postgres with
two organizations and the non-privileged role of the phase-4 rehearsal:

1. records that hold a canary value are in the store, and the canary is not
   in the model input;
2. an illegal target, a type mismatch and an unknown field are dropped;
3. no proposal is ever ``approved``;
4. the call goes through ``acompletion_with_fallback`` with
   ``feature="crm.field_map"``, and with the org bound;
5. a second press for the same profile version makes no model call.

No test makes a real model call. ``acompletion_with_fallback`` is a spy, and
one hermetic test goes one level down to the Router (H-171) with the Console
call replaced.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.settings import get_settings
from sqlalchemy import text

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _age_profile,
    _imported,
    _member_of,
    _req,
    _run,
    fr,
)
from tests.unit.test_crm_sync_engine_r8 import _count, _q, ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: A record value that must never reach the model.
CANARY = "CANARY-7f3a-record-value"

ADMIN = "admin@crm-sync.test"


def _module() -> Any:
    from gateway.routes.crm import field_rules

    return field_rules


def _row(key: str, label: str, kind: str, options: Any = None) -> Any:
    return SimpleNamespace(key=key, label=label, type=kind, options=options)


def _answer(content: str) -> Any:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ModelSpy:
    """``acompletion_with_fallback`` with no model behind it. It records the
    keyword arguments, the bound tenant and the run context of each call."""

    def __init__(self, items: Any = None, *, content: str | None = None,
                 error: BaseException | None = None, answer: Any = None,
                 delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._content = content if content is not None else json.dumps(items or [])
        self._error = error
        #: ``answer(payload) -> str`` reads the input of each call.
        self._answer = answer
        self._delay = delay

    async def __call__(self, **kw: Any) -> tuple[Any, str]:
        import asyncio

        from acb_common._log import get_run_context
        from acb_common.db import current_tenant, get_engine

        self.calls.append({
            "kw": kw, "tenant": current_tenant(), "ctx": dict(get_run_context()),
            "checked_out": get_engine().pool.checkedout(),
        })
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return _answer(self._answer(json.loads(kw["messages"][-1]["content"]))), "fake/model"
        return _answer(self._content), "fake/model"


class FakeRedis:
    """The raw client under the real ``TenantRedis`` wrapper: ``SET`` with
    ``nx`` and ``ex``, and nothing else."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, dict[str, Any]]] = []
        self.fail: BaseException | None = None

    async def set(self, name: str, value: str, **kw: Any) -> bool | None:
        self.sets.append((name, kw))
        if self.fail is not None:
            raise self.fail
        if kw.get("nx") and name in self.store:
            return None
        self.store[name] = value
        return True


@pytest.fixture(autouse=True)
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Every press runs its cool-down on the fake, through the real wrapper."""
    from acb_common.tenant_redis import TenantRedis

    fake = FakeRedis()
    monkeypatch.setattr(_module(), "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


def _spy(monkeypatch: pytest.MonkeyPatch, spy: ModelSpy) -> ModelSpy:
    import acb_llm.context as context

    monkeypatch.setattr(context, "acompletion_with_fallback", spy)
    return spy


def _input(spy: ModelSpy, index: int = 0) -> dict[str, Any]:
    """The JSON payload of the user message of one call."""
    messages = spy.calls[index]["kw"]["messages"]
    return json.loads(messages[-1]["content"])


# ── The pure rules (no database) ─────────────────────────────────────────────


def test_the_prompt_builder_takes_only_the_field_rows_and_the_targets() -> None:
    """🔴 The input holds metadata only because the builder can see nothing
    else. A third argument (a session, a record) fails here."""
    params = list(inspect.signature(_module().build_field_map_prompt).parameters)
    assert params == ["fields", "targets"]


def test_the_prompt_holds_labels_types_options_and_the_legal_targets() -> None:
    from gateway.crm_sources.zoho import mapping

    m = _module()
    options = [{"label": f"Option {i} " + "x" * 80, "value": f"v{i}"} for i in range(25)]
    targets = [mapping.FIELD_RULE_TARGETS["lead"][c] for c in ("industry", "no_of_employees")]
    messages = m.build_field_map_prompt(
        [_row("Custom_Industry", "Sector", "select", options), _row("Size", "Size", "number")],
        targets,
    )
    assert [msg["role"] for msg in messages] == ["system", "user"]
    payload = json.loads(messages[1]["content"])
    sector, size = payload["fields"]
    assert (sector["api_name"], sector["label"], sector["type"]) == (
        "Custom_Industry", "Sector", "select")
    assert len(sector["options"]) == m.MAX_PROMPT_OPTIONS == 20
    assert all(len(o) <= m.MAX_OPTION_CHARS == 60 for o in sector["options"])
    assert sector["may_fill"] == ["industry"]
    assert size["may_fill"] == ["no_of_employees"]
    assert payload["targets"] == [
        {"column": "industry", "type": "TEXT"},
        {"column": "no_of_employees", "type": "INT"},
    ]


@pytest.mark.parametrize(("content", "expected"), [
    ('[{"field_api_name": "A", "target_column": "industry"}]', 1),
    ('```json\n[{"field_api_name": "A", "target_column": "industry"}]\n```', 1),
    ('{"mappings": [{"field_api_name": "A", "target_column": "industry"}, 7]}', 1),
    ("[]", 0),
])
def test_an_answer_is_read_as_a_list_of_objects(content: str, expected: int) -> None:
    items = _module().parse_proposals(content)
    assert items is not None and len(items) == expected


@pytest.mark.parametrize("content", ["", "no json", '{"a": 1}', '"text"', "{"])
def test_a_bad_answer_reads_as_none(content: str) -> None:
    assert _module().parse_proposals(content) is None


def test_the_server_drops_an_unknown_field_an_illegal_target_and_a_type_mismatch() -> None:
    m = _module()
    items = [
        {"field_api_name": "Not_A_Field", "target_column": "industry"},
        {"field_api_name": "Sector", "target_column": "lead_name"},
        {"field_api_name": "Sector", "target_column": "no_of_employees"},
        {"field_api_name": "Sector", "target_column": "email"},
        {"field_api_name": "Sector", "target_column": "industry",
         "confidence": 1.7, "reason": "x" * 300, "status": "approved"},
        {"field_api_name": "Sector", "target_column": "lost_note"},
        {"field_api_name": "Size", "target_column": None},
    ]
    kept, dropped = m.keep_proposals(
        "lead", items, {"Sector": "select", "Size": "number"},
        {"industry", "no_of_employees", "lost_note", "lead_name"}, layout=["Email"],
    )
    assert [(p.field_api_name, p.target_column) for p in kept] == [("Sector", "industry")]
    assert dropped == 6
    assert kept[0].confidence == 1.0, "a confidence is clamped to 0..1"
    assert len(kept[0].reason or "") == m.MAX_REASON_CHARS
    assert not hasattr(kept[0], "status"), "an item never carries a status"


def test_the_write_of_a_proposal_names_proposed_and_never_approved() -> None:
    """🔴 Acceptance 3 in the SQL itself. The only status the proposer writes is
    a literal ``'proposed'``, and it replaces only an older proposal."""
    sql = _module()._PROPOSE_SQL
    assert "'proposed', 'ai'" in sql
    assert "'approved'" not in sql
    assert "WHERE crm_field_rules.status = 'proposed' AND crm_field_rules.proposed_by = 'ai'" in sql


def test_the_call_names_the_feature_and_a_tier_that_is_not_decide() -> None:
    m = _module()
    assert m.SUGGEST_FEATURE == "crm.field_map"
    assert m.SUGGEST_MODEL == "tier-fast", "tier-decide has an open residency (D75)"


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A box that bills, as ``test_internal_ai_is_routed.py`` sets one up."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_on_a_box_that_bills_the_call_reaches_the_router_with_the_member_and_feature(
    monkeypatch: pytest.MonkeyPatch, routed: Any,
) -> None:
    """One level below the spy: the real ``acompletion_with_fallback`` hands
    the call to the Console, so the Router meters it (H-171). The member is
    the admin and the agent is the feature."""
    pytest.importorskip("litellm")
    import litellm
    from acb_auth import console_resolve
    from acb_common._log import get_run_context

    calls: list[tuple[dict, dict]] = []

    async def console(payload: dict, **kw: Any) -> tuple[int, dict]:
        calls.append((payload, kw))
        return 200, {
            "id": "c1", "model": "deepseek/x",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "[]"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }

    async def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the call reached litellm, so it billed nobody")

    monkeypatch.setattr(console_resolve, "chat_completion_on_console", console)
    monkeypatch.setattr(litellm, "acompletion", boom)
    user = SimpleNamespace(email=ADMIN)
    content = await _module()._ask_model(user, [{"role": "user", "content": "x"}])
    assert content == "[]"
    payload, kw = calls[0]
    assert payload["model"] == "tier-fast"
    assert kw["member"] == ADMIN
    assert kw["agent"] == "crm.field_map"
    assert "user" not in get_run_context(), "the run context of the press is restored"


# ── R8: the route on a real Postgres ─────────────────────────────────────────


async def _suggest(org: str, entity: str = "leads") -> Any:
    return await _req(_admin_of(org), "POST", "/crm/field-rules/suggest", {"entity": entity})


def _ai_rules(p: Any, org: str) -> list[tuple[str, str, str, str]]:
    return [tuple(r) for r in _q(
        p, "SELECT field_api_name, target_column, status, proposed_by FROM crm_field_rules "
           "WHERE organization_id = CAST(:o AS uuid) AND proposed_by = 'ai' "
           "ORDER BY field_api_name", o=org)]


_GOOD = [
    {"field_api_name": "Custom_Industry", "target_column": "industry",
     "confidence": 0.9, "reason": "The labels name an industry."},
    {"field_api_name": "Headcount", "target_column": "no_of_employees",
     "confidence": 0.8, "reason": "A count of people."},
]


@_DB_GATE
async def test_1_a_canary_in_the_records_never_reaches_the_model(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    a.add("Leads", "l9", 9, Last_Name=CANARY, Company=CANARY,
          Custom_Industry=CANARY, Why_Lost=CANARY, Headcount=7)
    a.add("Contacts", "c9", 9, Last_Name=CANARY, LinkedIn=CANARY)
    await _imported(p, p.org_a, a)
    stored = _q(p, "SELECT count(*) FROM crm_leads WHERE organization_id = CAST(:o AS uuid) "
                   "AND custom::text LIKE :c", o=p.org_a, c=f"%{CANARY}%")[0][0]
    assert stored >= 1, "the canary must be in the store, or its absence proves nothing"

    spy = _spy(monkeypatch, ModelSpy(_GOOD))
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    assert len(spy.calls) == 1
    sent = json.dumps(spy.calls[0]["kw"], default=str)
    assert CANARY not in sent, "a record value reached the model"
    # The input is the metadata of the fields that go to custom.
    fields = {f["api_name"]: f for f in _input(spy)["fields"]}
    assert set(fields) == {"Custom_Industry", "Headcount", "Why_Lost"}
    assert fields["Custom_Industry"]["label"] == "Sector"
    assert fields["Custom_Industry"]["options"] == ["Robotics", "Drones"]


@_DB_GATE
async def test_2_an_illegal_target_a_type_mismatch_and_an_unknown_field_are_dropped(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    spy = _spy(monkeypatch, ModelSpy([
        {"field_api_name": "Not_A_Field", "target_column": "industry"},
        {"field_api_name": "Why_Lost", "target_column": "lead_name"},
        {"field_api_name": "Custom_Industry", "target_column": "no_of_employees"},
        # Email is on the layout, so the column is not open.
        {"field_api_name": "Why_Lost", "target_column": "email"},
        *_GOOD,
    ]))
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["asked"], body["dropped"], body["entity"]) == (True, 4, "lead")
    assert {(r["field_api_name"], r["target_column"]) for r in body["proposals"]} == {
        ("Custom_Industry", "industry"), ("Headcount", "no_of_employees")}
    assert _ai_rules(p, p.org_a) == [
        ("Custom_Industry", "industry", "proposed", "ai"),
        ("Headcount", "no_of_employees", "proposed", "ai"),
    ]
    targets = {t["column"] for t in _input(spy)["targets"]}
    assert "email" not in targets and "lead_name" not in targets
    assert "industry" in targets


@_DB_GATE
async def test_3_no_proposal_is_ever_approved(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    # An admin decided Why_Lost by hand. The proposer must not touch it.
    made = await _req(_admin_of(p.org_a), "POST", "/crm/field-rules", {
        "entity": "leads", "field_api_name": "Why_Lost", "target_column": "lost_note"})
    assert made.status_code == 201, made.text
    spy = _spy(monkeypatch, ModelSpy([
        {**item, "status": "approved", "proposed_by": "admin"} for item in _GOOD
    ] + [{"field_api_name": "Why_Lost", "target_column": "description", "status": "approved"}]))
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    assert {r["status"] for r in got.json()["proposals"]} == {"proposed"}
    assert {r["proposed_by"] for r in got.json()["proposals"]} == {"ai"}
    assert "Why_Lost" not in {f["api_name"] for f in _input(spy)["fields"]}, (
        "a decided field is not asked about")
    assert "lost_note" not in {t["column"] for t in _input(spy)["targets"]}, (
        "a column that an approved rule fills is not offered")
    rows = _q(p, "SELECT field_api_name, status, proposed_by, target_column FROM "
                 "crm_field_rules WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY field_api_name", o=p.org_a)
    assert [tuple(r) for r in rows] == [
        ("Custom_Industry", "proposed", "ai", "industry"),
        ("Headcount", "proposed", "ai", "no_of_employees"),
        ("Why_Lost", "approved", "admin", "lost_note"),
    ]
    # Only the admin's approve makes a proposal approved.
    rule = next(r for r in got.json()["proposals"] if r["field_api_name"] == "Custom_Industry")
    approved = await _req(_admin_of(p.org_a), "PATCH", f"/crm/field-rules/{rule['id']}",
                          {"status": "approved"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"


@_DB_GATE
async def test_4_the_call_is_routed_with_the_feature_and_the_org_bound(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    spy = _spy(monkeypatch, ModelSpy(_GOOD))
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    call = spy.calls[0]
    assert call["kw"]["feature"] == "crm.field_map"
    assert call["kw"]["model"] == "tier-fast"
    assert call["tenant"] == p.org_a, "the organization is bound for the meter"
    assert call["ctx"].get("user") == ADMIN, "the admin is bound for the meter"
    assert call["ctx"].get("member_verified") == "1"


@_DB_GATE
async def test_5_a_second_press_for_the_same_profile_version_makes_no_model_call(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    spy = _spy(monkeypatch, ModelSpy(_GOOD))
    first = await _suggest(p.org_a)
    assert first.status_code == 200, first.text
    second = await _suggest(p.org_a)
    assert second.status_code == 200, second.text
    assert len(spy.calls) == 1, "the second press called the model"
    assert second.json()["asked"] is False
    assert {r["id"] for r in second.json()["proposals"]} == {
        r["id"] for r in first.json()["proposals"]}
    assert len(_ai_rules(p, p.org_a)) == 2


@_DB_GATE
async def test_a_member_gets_403_and_org_b_sees_no_proposal_of_org_a(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, b = fr
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    spy = _spy(monkeypatch, ModelSpy(_GOOD))
    # Every member may open this CRM, so the 403 below comes from the admin
    # guard of the route and not from the access rule of the router.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    assert (await _req(_member_of(p.org_a), "GET", "/crm/fields/leads")).status_code == 200
    member = await _req(_member_of(p.org_a), "POST", "/crm/field-rules/suggest",
                        {"entity": "leads"})
    assert member.status_code == 403
    assert spy.calls == [], "a refused press calls no model"
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    view = await _req(_admin_of(p.org_b), "GET", "/crm/field-rules?entity=leads")
    assert view.status_code == 200 and view.json()["rules"] == []
    assert _count(p, "crm_field_rules", p.org_b) == 0
    # A body that names an organization is refused (R11).
    forged = await _req(_admin_of(p.org_a), "POST", "/crm/field-rules/suggest",
                        {"entity": "leads", "organization_id": p.org_b})
    assert forged.status_code == 422


@_DB_GATE
async def test_a_model_error_or_a_bad_answer_gives_a_short_message_and_no_row(
    fr: Any, monkeypatch: pytest.MonkeyPatch, redis: FakeRedis,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    _spy(monkeypatch, ModelSpy(error=RuntimeError("upstream said no")))
    failed = await _suggest(p.org_a)
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "suggest_failed"
    assert "upstream" not in failed.text, "the message holds no provider text"

    redis.store.clear()  # the minute of the cool-down passed
    _spy(monkeypatch, ModelSpy(content="I think Sector is the industry."))
    bad = await _suggest(p.org_a)
    assert bad.status_code == 502
    assert bad.json()["detail"]["code"] == "suggest_bad_answer"
    assert _count(p, "crm_field_rules", p.org_a) == 0


@_DB_GATE
async def test_a_native_org_gets_409_and_calls_no_model(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    spy = _spy(monkeypatch, ModelSpy(_GOOD))
    got = await _suggest(p.org_a)
    assert got.status_code == 409
    assert spy.calls == []


# ── Review round 1 ───────────────────────────────────────────────────────────


@_DB_GATE
async def test_a_decided_proposal_does_not_stop_a_new_field_of_a_new_profile(
    fr: Any, monkeypatch: pytest.MonkeyPatch, redis: FakeRedis,
) -> None:
    """[P1] An approve stamps its row with the version of today. The row is
    decided, so it must not count as an open proposal of that version."""
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    spy = _spy(monkeypatch, ModelSpy([_GOOD[0]]))
    first = await _suggest(p.org_a)
    assert first.status_code == 200, first.text
    before = first.json()["profile_version"]
    rule_id = first.json()["proposals"][0]["id"]

    # Zoho gains a field, so discovery stores a new profile version.
    a.fields["Leads"].append(
        {"api_name": "Segment", "field_label": "Segment", "data_type": "text"})
    _age_profile(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    # An incremental run reads no field definitions. A reconcile writes the
    # row of the new field, and this insert stands in for it.
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, provider_api_name, position, visible) VALUES (CAST(:o AS uuid), "
            "'lead', 'Segment', 'Segment', 'text', 'zoho', 'Segment', 99, true)"),
            {"o": p.org_a})
    approved = await _req(_admin_of(p.org_a), "PATCH", f"/crm/field-rules/{rule_id}",
                          {"status": "approved"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["profile_version"] > before, "the approve takes the new version"

    redis.store.clear()  # the minute of the cool-down passed
    second = await _suggest(p.org_a)
    assert second.status_code == 200, second.text
    assert second.json()["asked"] is True, "a decided row stopped the new field"
    assert len(spy.calls) == 2
    assert "Segment" in {f["api_name"] for f in _input(spy, 1)["fields"]}


@_DB_GATE
async def test_an_approve_refuses_a_proposal_that_changed_after_the_read(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[P2] A new proposal can change the target between the read and the
    update of an approve. The update then changes nothing, and the admin
    gets 409."""
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    _spy(monkeypatch, ModelSpy([_GOOD[0]]))
    got = await _suggest(p.org_a)
    rule = got.json()["proposals"][0]
    assert rule["target_column"] == "industry"

    m = _module()
    original = m._check_free

    async def racing(db: Any, entity: str, column: str, field: str, rule_id: Any) -> None:
        await original(db, entity, column, field, rule_id)
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_field_rules SET target_column = 'lost_note' "
                           "WHERE id = CAST(:i AS uuid)"), {"i": rule["id"]})

    monkeypatch.setattr(m, "_check_free", racing)
    res = await _req(_admin_of(p.org_a), "PATCH", f"/crm/field-rules/{rule['id']}",
                     {"status": "approved"})
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {
        "code": "rule_changed", "message": "This proposal changed. Reload to see it."}
    row = _q(p, "SELECT status, target_column FROM crm_field_rules "
                "WHERE id = CAST(:i AS uuid)", i=rule["id"])[0]
    assert tuple(row) == ("proposed", "lost_note"), "the approve changed the new proposal"


@_DB_GATE
async def test_one_press_a_minute_for_each_org_and_entity(
    fr: Any, monkeypatch: pytest.MonkeyPatch, redis: FakeRedis,
) -> None:
    """[P2] The cool-down is a tenant Redis key with NX and a TTL of 60 s,
    set before the model call. A press that failed still counts."""
    p, a, b = fr
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    spy = _spy(monkeypatch, ModelSpy(error=RuntimeError("down")))
    assert (await _suggest(p.org_a)).status_code == 502
    again = await _suggest(p.org_a)
    assert again.status_code == 429, again.text
    assert again.json()["detail"] == {
        "code": "suggest_cooldown", "message": "Wait a minute before you ask again."}
    assert again.headers["retry-after"] == "60"
    assert len(spy.calls) == 1, "the second press called the model"
    name, kw = redis.sets[0]
    assert name == f"cc:{p.org_a}:crm-field-map:lead"
    assert kw == {"nx": True, "ex": 60}

    # Another entity and another organization each have a minute of their own.
    assert (await _suggest(p.org_a, "deals")).status_code == 502
    assert (await _suggest(p.org_b)).status_code == 502
    assert len(spy.calls) == 3

    # A Redis outage stops the press before the model, because the bound guards money.
    redis.store.clear()
    redis.fail = ConnectionError("redis down")
    out = await _suggest(p.org_a)
    assert out.status_code == 503
    assert out.json()["detail"]["code"] == "suggest_unavailable"
    assert len(spy.calls) == 3


@_DB_GATE
async def test_a_long_entity_goes_in_batches_and_each_answer_is_read_alone(
    fr: Any, monkeypatch: pytest.MonkeyPatch, redis: FakeRedis,
) -> None:
    """[P2] 88 fields make three calls of 40 fields at most. A bad answer in
    one batch loses that batch only, and the press counts once."""
    p, a, _b = fr
    a.fields["Leads"] += [
        {"api_name": f"Bulk_{i:03d}", "field_label": f"Bulk {i}", "data_type": "text"}
        for i in range(85)
    ]
    await _imported(p, p.org_a, a)

    def answer(payload: dict[str, Any]) -> str:
        names = [f["api_name"] for f in payload["fields"]]
        if "Bulk_050" in names:
            return "not json"
        return json.dumps([{"field_api_name": names[0], "target_column": "lost_note"}])

    spy = _spy(monkeypatch, ModelSpy(answer=answer))
    got = await _suggest(p.org_a)
    assert got.status_code == 200, got.text
    sizes = [len(_input(spy, i)["fields"]) for i in range(len(spy.calls))]
    assert len(sizes) == 3 and max(sizes) <= _module().SUGGEST_BATCH_FIELDS == 40
    assert sum(sizes) == 88
    assert len(redis.sets) == 1, "one press is one start of the cool-down"
    body = got.json()
    bad = next(s for i, s in enumerate(sizes)
               if "Bulk_050" in {f["api_name"] for f in _input(spy, i)["fields"]})
    assert body["asked"] is True and body["unread"] == bad
    assert len(body["proposals"]) == 2
    assert {r["status"] for r in body["proposals"]} == {"proposed"}


@_DB_GATE
async def test_the_call_times_out_with_its_message_and_holds_nothing(
    fr: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound of one call ends it with the 502 sentence. No session is open
    across the call, and no slot stays held."""
    from acb_common.db import get_engine
    from email_ingestion import llm_cap

    p, a, _b = fr
    await _imported(p, p.org_a, a)
    monkeypatch.setattr(_module(), "SUGGEST_TIMEOUT_S", 0.05)
    spy = _spy(monkeypatch, ModelSpy(_GOOD, delay=2.0))
    got = await _suggest(p.org_a)
    assert got.status_code == 502, got.text
    assert got.json()["detail"] == {
        "code": "suggest_failed",
        "message": "The assistant could not suggest mappings now. Try again later.",
    }
    assert spy.calls[0]["checked_out"] == 0, "a session stayed open across the model call"
    assert llm_cap.permits_in_use() == 0
    assert get_engine().pool.checkedout() == 0
    assert _count(p, "crm_field_rules", p.org_a) == 0
