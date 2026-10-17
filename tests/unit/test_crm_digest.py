"""WS-53 CRM-Q28 — the weekly CRM digest on the powerful tier (hermetic).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q28. The table,
its RLS, one digest each week, the Monday rule, the cap of 3 tries, the
route and the start over on a real database are in
``test_crm_digest_r8.py``.

* ``TestTheOrgList`` (item 3): ``CRM_DIGEST_ORGS`` is empty by default,
  ``flags.digest_allowed`` is the one check, and ``work_plan.md`` §6
  registers the flip.
* ``TestTheWeek`` (item 4): the Monday 06:00 rule in the CRM zone.
* ``TestTheQueries`` (item 5): a fixed tuple of 8 CRM-Q25 calls at most,
  through ``aggregate.compute``, the one body of the route.
* ``TestAMissingPart`` (item 6) and ``TestTheLabels`` (item 7).
* ``TestThePrompt`` (items 8 and 11): one request to ``tier-powerful``,
  ``max_tokens`` 800, the numbers as JSON, 8,000 characters at most.
* ``TestTheFormatter`` and ``TestTheNumberCheck`` (item 9): THE NUMBER RULE.
* ``TestThePayer`` (item 10): ``connected_by``, the departed rule, the job
  scope.
* ``TestTheStep`` (items 9, 11 and 16): a rejected text is not stored and
  the try counts, and a log line holds no text.
* ``TestTheScheduler`` (item 4).
* ``TestTheRoute`` (item 13) and ``TestTheMyDayCard`` (item 14).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_common.settings import Settings, get_settings
from gateway.crm_sync import digest as dg
from gateway.crm_sync import note_labels as nl
from gateway.crm_sync import scheduler
from gateway.reach import classes
from gateway.routes.crm import aggregate, core, digests, flags
from gateway.routes.shell import feed, feed_rank

from tests.unit._crm_fakes import crm_switch_on

REPO = Path(__file__).resolve().parents[2]
ORG = "28282828-0000-4000-8000-000000000001"
OTHER = "28282828-0000-4000-8000-000000000002"
MEMBER = "owner@crm-q28.test"

STEP = REPO / "apps/services/gateway/gateway/crm_sync/digest.py"
ROUTE = REPO / "apps/services/gateway/gateway/routes/crm/digests.py"
AGGREGATE = REPO / "apps/services/gateway/gateway/routes/crm/aggregate.py"

#: A Monday in October 2026, and the week before it.
MONDAY = date(2026, 10, 12)
WEEK = dg.Week(start=date(2026, 10, 5), end=date(2026, 10, 11), zone="UTC",
               close_from=date(2026, 10, 1), close_to=date(2026, 12, 31))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("CRM_DIGEST_ORGS", "DECIDE_FEATURE_MODES", "CRM_DECIDE_ORGS"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    scheduler._digesting.clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    scheduler._digesting.clear()


def _listed(monkeypatch, orgs: str = ORG) -> None:
    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_digest_orgs", orgs, raising=False)


def _numbers() -> dict[str, Any]:
    """The numbers of one week, as ``gather`` gives them."""
    return {
        "parts": [
            {"key": "open_deals_by_stage", "title": "Open deals by stage", "unit": "deals",
             "rows": [{"label": "Qualify", "value": "12"},
                      {"label": "Stage 2", "value": "7"}]},
            {"key": "amount_by_close_month", "title": "Amount by close month",
             "unit": "INR", "total": "INR 12,34,567.50",
             "rows": [{"label": "November 2026", "value": "INR 10,00,000"},
                      {"label": "December 2026", "value": "INR 2,34,567.50"}]},
            {"key": "new_leads", "title": "New leads in the week", "unit": "leads",
             "rows": [], "total": "4"},
        ],
        "missing": ["New contacts in the week"],
    }


class _Boom:
    """A seam that must not be reached. Any call raises."""

    def __init__(self, what: str) -> None:
        self.what = what

    def __call__(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError(f"{self.what} must not run")


# ── Item 3: the org list ────────────────────────────────────────────────────


class TestTheOrgList:
    def test_the_list_is_empty_by_default(self):
        assert Settings().crm_digest_orgs == ""

    def test_an_empty_list_allows_no_org(self, monkeypatch):
        crm_switch_on(monkeypatch, "*")
        assert flags.digest_allowed(ORG) is False
        assert dg.enabled_for(ORG) is False

    def test_a_named_org_and_a_star(self, monkeypatch):
        _listed(monkeypatch, f" {ORG.upper()} ")
        assert flags.digest_allowed(ORG) is True
        assert flags.digest_allowed(OTHER) is False
        _listed(monkeypatch, "*")
        assert flags.digest_allowed(OTHER) is True

    def test_the_crm_switch_comes_first(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "crm_digest_orgs", "*", raising=False)
        monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
        assert flags.digest_allowed(ORG) is False
        assert flags.digest_allowed(None) is False

    def test_work_plan_registers_the_flip(self):
        plan = (REPO / "project-docs/work_plan.md").read_text(encoding="utf-8")
        section = plan[plan.index("## 6."):]
        entry = section[section.index("**WS-53 — the CRM weekly digest.**"):]
        entry = entry[:entry.index("\n>\n> **", 10)]
        assert "CRM_DIGEST_ORGS" in entry and "enforcement-flip" in entry
        assert "CRM_DIGEST_EMAIL_ORGS" in entry

    async def test_an_org_off_the_list_spends_nothing(self, monkeypatch):
        crm_switch_on(monkeypatch, "*")
        monkeypatch.setattr(dg, "tenant_session", _Boom("a session"))
        monkeypatch.setattr(dg, "_complete", _Boom("the model"))
        got = await dg.digest_after_run(ORG)
        assert got.reason == "off"


# ── Item 4: the week and the Monday rule ────────────────────────────────────


class TestTheWeek:
    def test_before_monday_six_in_the_zone_nothing_is_due(self):
        assert dg.week_due(datetime(2026, 10, 12, 5, 59, tzinfo=UTC), "UTC") is None

    def test_at_monday_six_the_week_before_is_due(self):
        week = dg.week_due(datetime(2026, 10, 12, 6, 0, tzinfo=UTC), "UTC")
        assert week is not None
        assert (week.start, week.end) == (date(2026, 10, 5), date(2026, 10, 11))
        assert week.start.weekday() == 0 and week.end.weekday() == 6

    def test_the_rule_reads_the_crm_zone(self):
        # 01:00 UTC is 06:30 in Kolkata: due there, and not yet in UTC.
        now = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)
        assert dg.week_due(now, "UTC") is None
        assert dg.week_due(now, "Asia/Kolkata").start == date(2026, 10, 5)
        # 00:00 UTC is 05:30 in Kolkata: not yet.
        assert dg.week_due(datetime(2026, 10, 12, 0, 0, tzinfo=UTC), "Asia/Kolkata") is None

    def test_a_sunday_reads_the_week_that_ended_a_week_ago(self):
        week = dg.week_due(datetime(2026, 10, 18, 23, 0, tzinfo=UTC), "UTC")
        assert week.start == date(2026, 10, 5)

    def test_the_close_window_is_this_month_and_the_next_two(self):
        week = dg.week_due(datetime(2026, 11, 2, 7, 0, tzinfo=UTC), "UTC")
        assert (week.close_from, week.close_to) == (date(2026, 11, 1), date(2027, 1, 31))

    def test_the_bounds_are_midnight_in_the_zone(self):
        week = dg.week_due(datetime(2026, 10, 12, 1, 0, tzinfo=UTC), "Asia/Kolkata")
        start, end = week.bounds()
        assert start.astimezone(UTC) == datetime(2026, 10, 4, 18, 30, tzinfo=UTC)
        assert (end - start).days == 7


# ── Item 5: the fixed queries, through the one body of the route ────────────


class TestTheQueries:
    def test_eight_calls_at_most_written_in_code(self):
        assert 0 < len(dg.DIGEST_QUERIES) <= 8
        assert isinstance(dg.DIGEST_QUERIES, tuple)
        assert len({q.key for q in dg.DIGEST_QUERIES}) == len(dg.DIGEST_QUERIES)

    @pytest.mark.parametrize("query", dg.DIGEST_QUERIES, ids=lambda q: q.key)
    def test_each_query_is_a_legal_crm_q25_call(self, query):
        found, module = aggregate._check(query.entity, query.measure, query.group_by,
                                         None, None, None)
        assert found is not None and module is None
        assert query.show in ("groups", "total")
        params = dg._list_params(**query.filters(WEEK))
        assert params.direction == "desc" and params.page == 1 and params.custom == ()

    @pytest.mark.parametrize("query", dg.DIGEST_QUERIES, ids=lambda q: q.key)
    def test_no_query_groups_by_a_person_or_a_record(self, query):
        # Item 8: the prompt holds no email address, so no owner group.
        assert query.group_by in ("status", "close_month", "created_month")

    def test_each_title_holds_no_digit_and_no_number_word(self):
        titles = [q.title for q in dg.DIGEST_QUERIES] + [dg.LABELS_TITLE]
        for title in titles:
            assert not re.search(r"\d", title), title

    def test_the_route_and_the_step_run_one_body(self):
        route = ast.parse(AGGREGATE.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(route)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "aggregate")
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "compute" in calls
        assert "_read" not in calls
        step = STEP.read_text(encoding="utf-8")
        assert "from gateway.routes.crm.aggregate import compute" in step
        assert "await compute(" in step

    def test_the_step_writes_no_sql_over_a_record_table(self):
        source = STEP.read_text(encoding="utf-8")
        for table in ("crm_deals", "crm_leads", "crm_contacts", "crm_companies",
                      "crm_activities", "crm_module_records"):
            assert not re.search(rf"\bFROM {table}\b", source), table
        # The one other read names the stage types, never a number.
        assert re.findall(r"FROM (crm_\w+)", source) == ["crm_deal_statuses", "crm_digests"]

    async def test_gather_sends_each_query_through_compute(self, monkeypatch):
        seen: list[dict[str, Any]] = []

        async def fake_compute(entity, **kw):
            seen.append({"entity": entity, **kw})
            return aggregate.AggregateResponse(
                entity=entity, measure=kw["measure"], groups=[], total=3)

        async def fake_types(_org):
            return {}

        monkeypatch.setattr(aggregate, "compute", fake_compute)
        monkeypatch.setattr(dg, "_types", fake_types)
        monkeypatch.setattr(nl, "mode", lambda _org: "off")
        got = await dg.gather(ORG, WEEK)
        assert [s["entity"] for s in seen] == [q.entity for q in dg.DIGEST_QUERIES]
        assert [s["group_by"] for s in seen] == [q.group_by for q in dg.DIGEST_QUERIES]
        assert all("organization" not in k and k != "org" for s in seen for k in s)
        assert got["missing"] == []
        new_leads = next(p for p in got["parts"] if p["key"] == "new_leads")
        assert new_leads == {"key": "new_leads", "title": "New leads in the week",
                             "unit": "leads", "rows": [], "total": "3"}
        leads = next(s for s in seen if s["entity"] == "leads")
        assert leads["params"].include_converted is True
        assert leads["params"].created_from == "2026-10-05"
        assert leads["params"].created_to == "2026-10-11"


class TestThePart:
    def _answer(self, groups, total=None, other=0, omitted=0):
        return aggregate.AggregateResponse(
            entity="deals", measure="count", groups=[
                aggregate.AggregateGroup(key=k, label=lbl, value=v) for k, lbl, v in groups],
            total=total, other=other, omitted=omitted)

    def test_a_typed_part_keeps_its_types_and_no_total(self):
        query = next(q for q in dg.DIGEST_QUERIES if q.key == "won_lost")
        answer = self._answer([("w", "Won", 4), ("o", "Open", 9), ("l", "Lost", 2)], 15)
        part = dg._part(query, answer, {"w": "won", "o": "open", "l": "lost"})
        assert part["rows"] == [{"label": "Won", "value": "4"},
                                {"label": "Lost", "value": "2"}]
        # The total counts the open stage too, so it is no number of this part.
        assert "total" not in part

    def test_a_month_key_reads_as_words(self):
        query = next(q for q in dg.DIGEST_QUERIES if q.key == "amount_by_close_month")
        part = dg._part(query, self._answer([("2026-11", "2026-11", 1500.5)], 1500.5), {})
        assert part["rows"] == [{"label": "November 2026", "value": "INR 1,500.50"}]
        assert part["total"] == "INR 1,500.50"

    def test_the_folded_groups_are_the_answers_own_numbers(self):
        query = next(q for q in dg.DIGEST_QUERIES if q.key == "amount_by_close_month")
        part = dg._part(query, self._answer([("2026-11", "2026-11", 10)], 25, other=15,
                                            omitted=2), {})
        assert part["rows"][-1] == {"label": "All other groups", "value": "INR 15"}
        assert part["other_groups"] == "2"

    def test_an_empty_measure_reads_no_values_recorded(self):
        query = next(q for q in dg.DIGEST_QUERIES if q.key == "amount_by_close_month")
        part = dg._part(query, self._answer([("2026-11", "2026-11", None)], None), {})
        assert part["rows"][0]["value"] == dg.NO_VALUES
        assert part["total"] == dg.NO_VALUES


# ── Item 6: a part that fails ───────────────────────────────────────────────


class TestAMissingPart:
    async def test_a_refused_or_slow_query_is_named_and_left_out(self, monkeypatch):
        from fastapi import HTTPException

        async def fake_compute(entity, **kw):
            if kw["group_by"] == "created_month":
                raise HTTPException(status_code=422, detail={"code": "created_range_mirror"})
            if kw["measure"] == "sum_amount":
                raise HTTPException(status_code=503, detail=aggregate.TIMEOUT_DETAIL)
            return aggregate.AggregateResponse(entity=entity, measure="count",
                                               groups=[], total=1)

        async def fake_types(_org):
            return {}

        monkeypatch.setattr(aggregate, "compute", fake_compute)
        monkeypatch.setattr(dg, "_types", fake_types)
        monkeypatch.setattr(nl, "mode", lambda _org: "off")
        got = await dg.gather(ORG, WEEK)
        keys = {p["key"] for p in got["parts"]}
        assert keys == {"open_deals_by_stage", "won_lost"}
        assert "New leads in the week" in got["missing"]
        assert "Amount of the open deals by stage" in got["missing"]
        prompt = dg.messages_for(got)[1]["content"]
        assert "New leads in the week" in prompt and '"missing"' in prompt
        assert "missing" in dg.SYSTEM


# ── Item 7: the labels, in ``on`` only ──────────────────────────────────────


class TestTheLabels:
    async def _gather(self, monkeypatch, mode: str) -> tuple[dict[str, Any], list[Any]]:
        calls: list[Any] = []

        async def fake_compute(entity, **kw):
            return aggregate.AggregateResponse(entity=entity, measure="count",
                                               groups=[], total=1)

        async def fake_types(_org):
            return {}

        async def fake_counts(db, org, start, end):
            calls.append((org, start, end))
            return [{"kind": "payment", "notes": 3, "needs_action": 1}]

        @contextlib.asynccontextmanager
        async def fake_session(_org):
            yield object()

        monkeypatch.setattr(aggregate, "compute", fake_compute)
        monkeypatch.setattr(dg, "_types", fake_types)
        monkeypatch.setattr(dg, "tenant_session", fake_session)
        monkeypatch.setattr(nl, "mode", lambda _org: mode)
        monkeypatch.setattr(nl, "week_counts", fake_counts)
        return await dg.gather(ORG, WEEK), calls

    async def test_on_counts_the_notes_of_the_week(self, monkeypatch):
        got, calls = await self._gather(monkeypatch, "on")
        part = next(p for p in got["parts"] if p["key"] == dg.LABELS_KEY)
        assert part["rows"] == [{"label": "Payment notes", "value": "3"},
                                {"label": "Payment notes that need action", "value": "1"}]
        assert len(calls) == 1 and calls[0][2] - calls[0][1] == WEEK.bounds()[1] - WEEK.bounds()[0]

    @pytest.mark.parametrize("mode", ["shadow", "off"])
    async def test_shadow_and_off_read_no_label(self, monkeypatch, mode):
        got, calls = await self._gather(monkeypatch, mode)
        assert calls == []
        assert all(p["key"] != dg.LABELS_KEY for p in got["parts"])


# ── Items 8 and 11: the prompt ──────────────────────────────────────────────


class TestThePrompt:
    def test_the_prompt_holds_the_numbers_as_json_and_the_rule(self):
        messages = dg.messages_for(_numbers())
        assert [m["role"] for m in messages] == ["system", "user"]
        assert "Use only the numbers in the JSON. Add no number." in messages[0]["content"]
        body = messages[1]["content"].split("\n", 1)[1]
        assert json.loads(body) == dg.payload(_numbers())

    def test_the_prompt_holds_no_week_date_no_zone_and_no_id(self):
        stored = dg.stored_numbers(WEEK, _numbers())
        prompt = json.dumps(dg.messages_for(stored))
        assert "2026-10-05" not in prompt and "UTC" not in prompt
        assert set(dg.payload(stored)) == {"parts", "missing"}

    def test_the_input_never_passes_8000_characters(self):
        big = {"parts": [{"key": f"k{i}", "title": "T", "unit": "deals", "rows": [
            {"label": f"Stage name number {j} " + "x" * 40, "value": str(j)}
            for j in range(50)]} for i in range(8)], "missing": []}
        fitted = dg.fit(big)
        assert dg.input_chars(dg.messages_for(fitted)) <= dg.INPUT_CHARS
        assert any(p.get("cut") for p in fitted["parts"])
        # The cut rows are gone from the input AND from the stored numbers.
        assert dg.input_chars(dg.messages_for(big)) > dg.INPUT_CHARS

    def test_a_small_input_is_not_cut(self):
        assert dg.fit(_numbers()) == _numbers()

    async def test_one_request_to_the_powerful_tier_with_800_tokens(self, monkeypatch):
        from acb_llm import context

        seen: list[dict[str, Any]] = []

        async def fake(**kw):
            seen.append(kw)
            return (SimpleNamespace(id="req-1", choices=[SimpleNamespace(
                message=SimpleNamespace(content="Open deals: 12."))]), "x")

        monkeypatch.setattr(context, "acompletion_with_fallback", fake)
        body, rid = await dg._complete(dg.messages_for(_numbers()))
        assert (body, rid) == ("Open deals: 12.", "req-1")
        assert len(seen) == 1
        assert seen[0]["model"] == "tier-powerful" and dg.TIER == "tier-powerful"
        assert seen[0]["max_tokens"] == 800 == dg.MAX_TOKENS
        assert seen[0]["feature"] == "crm.digest"
        assert "fallback_model" not in seen[0]

    def test_the_cost_bound_is_three_requests_of_bounded_size(self):
        assert dg.MAX_TRIES == 3
        assert dg.INPUT_CHARS == 8000
        # About 2,000 tokens in and 800 out, 3 times: about 8,400 tokens.
        assert dg.MAX_TRIES * (dg.INPUT_CHARS // 4 + dg.MAX_TOKENS) == 8400

    def test_the_migration_holds_the_cap_of_three(self):
        found = [f for f in (REPO / "infra/postgres").glob("*.sql")
                 if "CREATE TABLE IF NOT EXISTS crm_digests" in f.read_text(encoding="utf-8")]
        assert len(found) == 1
        sql = found[0].read_text(encoding="utf-8")
        assert f"tries <= {dg.MAX_TRIES}" in sql


# ── Item 9: THE NUMBER RULE ─────────────────────────────────────────────────


class TestTheFormatter:
    @pytest.mark.parametrize(("value", "unit", "text"), [
        (12, "deals", "12"), (12.0, "deals", "12"), (1234567, "deals", "12,34,567"),
        (1234567.5, "INR", "INR 12,34,567.50"), (0.125, "deals", "0.13"),
        (1500.10, "INR", "INR 1,500.10"), (0, "INR", "INR 0"),
        (None, "INR", dg.NO_VALUES), (10 ** 9, "deals", "1,00,00,00,000"),
    ])
    def test_one_text_for_each_number(self, value, unit, text):
        assert dg.fmt(value, unit) == text

    def test_the_text_is_the_one_shared_formatter(self):
        from acb_common.number_check import MONEY, PLAIN, format_figure

        assert dg.fmt(1234567, "INR") == format_figure(1234567, MONEY)
        assert dg.fmt(42, "deals") == format_figure(42, PLAIN)

    def test_no_value_is_never_zero(self):
        assert dg.fmt(None) != "0" and dg.fmt(None, "INR") == "no values recorded"

    def test_a_boolean_is_no_number(self):
        with pytest.raises(TypeError):
            dg.fmt(True)

    def test_the_step_holds_no_formatter_and_no_check_of_its_own(self):
        tree = ast.parse(STEP.read_text(encoding="utf-8"))
        imports = {(n.module, a.name) for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom) for a in n.names}
        assert ("acb_common.number_check", "format_figure") in imports
        assert ("acb_common", "number_check") in imports
        source = STEP.read_text(encoding="utf-8")
        assert "number_check.check(" in source
        # No second formatter: no format spec, no round(), no group rule.
        assert not re.search(r"\{[^{}]*:[,.]\d*f\}", source)
        assert "round(" not in source and "group_indian" not in source
        # One regex with a digit class in the step, the month key, and no other.
        patterns = [n.args[0].value for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "compile"
                    and n.args and isinstance(n.args[0], ast.Constant)]
        assert [p for p in patterns if r"\d" in p] == [dg._MONTH_KEY.pattern]


class TestTheNumberCheck:
    @pytest.mark.parametrize("text", [
        "This week 12 deals sit in Qualify and 7 in Stage 2.",
        "The amount for November 2026 is INR 10,00,000, and INR 2,34,567.50 for "
        "December 2026.",
        "In total INR 12,34,567.50 closes in the window. 4 new leads came in.",
        "New contacts in the week: the numbers are not available.",
        "Qualify holds 12 deals.",
        # The shared rule: a figure matches at the precision that it states.
        "About INR 12.3 lakh closes in the window.",
    ])
    def test_a_text_with_only_input_numbers_passes(self, text):
        assert dg.check(text, _numbers()) == []

    @pytest.mark.parametrize(("text", "bad"), [
        # A sum the model made: 12 + 7.
        ("The open stages hold 19 deals.", "19"),
        # A share the model made.
        ("Qualify holds 63% of the open deals.", "63%"),
        # Money at a precision that no source gives.
        ("The window holds INR 15 lakh.", "15 lakh"),
        ("The pipeline grew by 98765 deals.", "98765"),
        # Money never matches a percent.
        ("November 2026 grew by 10,00,000%.", "10,00,000%"),
    ])
    def test_a_number_that_is_not_in_the_input_fails(self, text, bad):
        got = dg.check(text, _numbers())
        assert got and any(bad in g for g in got), got

    def test_an_empty_field_must_not_read_zero(self):
        numbers = {"parts": [{"key": "x", "title": "T", "unit": "INR",
                              "rows": [{"label": "Won", "value": dg.NO_VALUES}]}],
                   "missing": []}
        assert dg.check("Won: no values recorded.", numbers) == []
        assert dg.check("Won: INR 0.", numbers) == ["INR 0"]

    def test_the_sources_are_the_prompts_own_numbers(self):
        """The check reads the same JSON that the prompt holds, so a number
        of the system text or of the week is no source."""
        book = dg.sources_for(_numbers())
        prompt = dg.messages_for(_numbers())[1]["content"]
        assert json.loads(prompt.split("\n", 1)[1]) == dg.payload(_numbers())
        assert len(book) > 0
        assert dg.check("The week holds 800 deals.", _numbers()) == ["800"]

    def test_the_known_limit_a_word_is_not_read(self):
        """CRM-Q29 records it: the one check reads digits only. So the prompt
        asks for digits, and this test pins the limit, not a wish."""
        assert dg.check("Two stages hold deals.", _numbers()) == []
        assert "Never write a number as a word." in dg.SYSTEM


# ── Item 10: the payer ──────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def _session(_org: str):
    class _Db:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(first=lambda: None)
    yield _Db()


def _step_fakes(monkeypatch, *, member: str | None = MEMBER, left: str | None = None,
                replies: list[str] | None = None) -> dict[str, Any]:
    """The step with every seam faked: the zone, the row, the payer, the
    numbers, the try counter and the model. Answers the record of calls."""
    state: dict[str, Any] = {"calls": [], "tries": 0, "stored": None, "failed": [],
                             "scope": []}
    _listed(monkeypatch)

    async def crm_zone(_db, _org):
        return "UTC"

    async def member_of(_db, _org):
        return member

    async def member_left(_org, _member):
        return left

    async def gather(_org, _week):
        return _numbers()

    async def reserve(_org, _week, _numbers):
        if state["tries"] >= dg.MAX_TRIES:
            return None
        state["tries"] += 1
        return state["tries"]

    async def complete(messages):
        state["calls"].append(messages)
        state["scope"].append(dict(structlog.contextvars.get_contextvars()))
        script = replies or ["Qualify holds 12 deals."]
        return script[min(len(state["calls"]), len(script)) - 1], f"req-{len(state['calls'])}"

    async def store(_org, _week, numbers, body, rid):
        state["stored"] = (body, rid, numbers)
        return True

    async def failed(_org, _week, rid):
        state["failed"].append(rid)

    import gateway.routes.crm.field_sql as field_sql

    monkeypatch.setattr(field_sql, "crm_zone", crm_zone)
    monkeypatch.setattr(dg, "tenant_session", _session)
    monkeypatch.setattr(nl, "_member", member_of)
    monkeypatch.setattr(nl, "_member_left", member_left)
    monkeypatch.setattr(dg, "gather", gather)
    monkeypatch.setattr(dg, "reserve_try", reserve)
    monkeypatch.setattr(dg, "_complete", complete)
    monkeypatch.setattr(dg, "_store", store)
    monkeypatch.setattr(dg, "_failed", failed)
    return state


NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


class TestThePayer:
    async def test_no_member_skips_and_spends_nothing(self, monkeypatch):
        state = _step_fakes(monkeypatch, member=None)
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "no_member" and state["calls"] == [] and state["tries"] == 0

    async def test_a_member_who_left_skips_and_spends_nothing(self, monkeypatch):
        state = _step_fakes(monkeypatch, left="other_org")
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "member_left" and state["calls"] == [] and state["tries"] == 0

    async def test_the_call_runs_as_the_member_of_the_connection(self, monkeypatch):
        state = _step_fakes(monkeypatch)
        await dg.digest_after_run(ORG, now=NOW)
        [scope] = state["scope"]
        assert scope.get("user") == MEMBER
        assert scope.get("member_verified") in (True, "1")
        assert scope.get("app") == "crm"


# ── Items 9, 11 and 16: the step ────────────────────────────────────────────


class TestTheStep:
    async def test_a_text_that_passes_is_stored_with_its_numbers(self, monkeypatch):
        state = _step_fakes(monkeypatch)
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "stored" and got.requests == 1 and got.tries == 1
        assert got.week == "2026-10-05"
        body, rid, numbers = state["stored"]
        assert (body, rid) == ("Qualify holds 12 deals.", "req-1")
        assert numbers == dg.fit(_numbers())

    async def test_a_bad_number_is_not_stored_and_the_text_is_written_again(
            self, monkeypatch):
        state = _step_fakes(monkeypatch, replies=["The stages hold 19 deals.",
                                                  "Qualify holds 12 deals."])
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "stored" and got.requests == 2 and got.rejected == 1
        assert state["failed"] == ["req-1"]
        assert state["stored"][0] == "Qualify holds 12 deals."

    async def test_three_bad_texts_store_nothing_and_stop_at_the_cap(self, monkeypatch):
        state = _step_fakes(monkeypatch, replies=["The stages hold 19 deals."])
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "tries_cap" and got.requests == 3 and got.rejected == 3
        assert state["stored"] is None and len(state["calls"]) == 3
        # The next run of the week spends nothing.
        again = await dg.digest_after_run(ORG, now=NOW)
        assert again.requests == 0 and len(state["calls"]) == 3

    async def test_an_empty_text_counts_a_try(self, monkeypatch):
        state = _step_fakes(monkeypatch, replies=["  ", "Qualify holds 12 deals."])
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.rejected == 1 and state["stored"][0] == "Qualify holds 12 deals."

    async def test_no_answer_stops_the_step(self, monkeypatch):
        state = _step_fakes(monkeypatch)

        async def broken(_messages):
            state["calls"].append(1)
            raise TimeoutError("the vendor")

        monkeypatch.setattr(dg, "_complete", broken)
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "no_answer" and got.requests == 1 and state["tries"] == 1

    async def test_no_numbers_send_no_request(self, monkeypatch):
        state = _step_fakes(monkeypatch)

        async def nothing(_org, _week):
            return {"parts": [], "missing": [q.title for q in dg.DIGEST_QUERIES]}

        monkeypatch.setattr(dg, "gather", nothing)
        got = await dg.digest_after_run(ORG, now=NOW)
        assert got.reason == "no_numbers" and state["calls"] == [] and state["tries"] == 0

    async def test_before_monday_six_nothing_runs(self, monkeypatch):
        state = _step_fakes(monkeypatch)
        got = await dg.digest_after_run(ORG, now=datetime(2026, 10, 12, 5, 0, tzinfo=UTC))
        assert got.reason == "not_yet" and state["calls"] == []

    async def test_a_log_line_holds_no_text_and_no_number(self, monkeypatch):
        _step_fakes(monkeypatch, replies=["SECRET-TEXT holds 19 deals.",
                                          "SECRET-TEXT Qualify holds 12 deals."])
        with structlog.testing.capture_logs() as logs:
            await dg.digest_after_run(ORG, now=NOW)
        dump = json.dumps(logs, default=str)
        assert "SECRET-TEXT" not in dump
        assert "Qualify" not in dump and "1234567.5" not in dump
        step = next(e for e in logs if e["event"] == "crm.digest.step")
        assert step["organization_id"] == ORG and step["week"] == "2026-10-05"
        assert step["request_ids"] == ["req-1", "req-2"]

    def test_no_session_is_open_across_the_model_call(self):
        tree = ast.parse(STEP.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_digest")
        for node in ast.walk(fn):
            if isinstance(node, ast.AsyncWith):
                inner = {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call)
                         and isinstance(c.func, ast.Name)}
                assert "_complete" not in inner


# ── Item 4: the scheduler ───────────────────────────────────────────────────


class TestTheScheduler:
    async def test_an_org_off_the_list_starts_no_task(self, monkeypatch):
        crm_switch_on(monkeypatch, "*")
        assert scheduler.start_digest(ORG) is None

    async def test_a_listed_org_starts_one_step_at_a_time(self, monkeypatch):
        _listed(monkeypatch)
        gate = asyncio.Event()
        runs: list[str] = []

        async def fake(org, *, should_stop):
            runs.append(org)
            await gate.wait()
            return dg.Digested("stored")

        monkeypatch.setattr(dg, "digest_after_run", fake)
        task = scheduler.start_digest(ORG)
        assert task is not None
        assert scheduler.start_digest(ORG) is None
        gate.set()
        await task
        await asyncio.sleep(0)
        assert runs == [ORG] and ORG not in scheduler._digesting

    def test_both_run_ends_start_the_digest(self):
        source = (REPO / "apps/services/gateway/gateway/crm_sync/scheduler.py").read_text(
            encoding="utf-8")
        assert source.count("start_digest(org)") == 1
        assert source.count("start_digest(handle.org)") == 1


# ── Item 13: the route ──────────────────────────────────────────────────────


class TestTheRoute:
    def test_the_route_sits_on_the_crm_router_with_its_guards(self):
        route = next(r for r in core.router.routes
                     if getattr(r, "path", "") == "/crm/digests")
        assert route.methods == {"GET"}
        deps = [d.dependency for d in core.router.dependencies]
        from gateway.routes.crm.access import require_crm_access

        assert require_crm_access in deps and flags.require_crm_enabled in deps

    def test_the_route_reads_only_a_stored_text_of_the_bound_org(self):
        assert "body IS NOT NULL" in digests._LIST_SQL
        assert "organization_id = CAST(:o AS uuid)" in digests._LIST_SQL
        source = ROUTE.read_text(encoding="utf-8")
        assert "request." not in source and "Header(" not in source

    async def test_the_limit_is_bounded(self):
        import inspect

        sig = inspect.signature(digests.list_digests)
        limit = sig.parameters["limit"].default
        assert limit.default == 4
        assert digests.MAX_LIMIT == 12


# ── Item 14: the My Day card ────────────────────────────────────────────────


class TestTheMyDayCard:
    def test_the_kind_is_registered_everywhere(self):
        assert "digest_ready" in feed.CRM_KINDS
        assert feed_rank.BASE["digest_ready"] < min(
            v for k, v in feed_rank.BASE.items() if k != "digest_ready")
        assert feed_rank.group("digest_ready", {}, NOW, NOW.date()) == "fyi"
        # Delivery outside the app is CRM-Q28b, an owner gate.
        assert classes.REACH_CLASS["digest_ready"] == "none"

    def test_the_signal_opens_reports_and_holds_no_text(self):
        signal = feed._digest_signal({"week_start": date(2026, 10, 5),
                                      "stored_at": NOW})
        assert signal.signal_id == "crm:digest:2026-10-05"
        assert signal.href == "/crm?tab=reports"
        assert [a["href"] for a in signal.acts] == ["/crm?tab=reports"]
        assert set(signal.facts) == {"week_start", "stored_at"}

    def test_the_why_line_is_a_code_template(self):
        why = feed._md3_why("digest_ready", {"week_start": "2026-10-05"},
                            NOW.tzinfo, NOW.date())
        assert why == ("digest_ready",
                       "The CRM digest of the week from 5 Oct is ready in Reports.")
        assert feed._md3_why("digest_ready", {}, NOW.tzinfo, NOW.date())[1] == (
            "The weekly CRM digest is ready in Reports.")

    def test_the_feed_reads_the_digest_only_for_a_listed_org(self):
        source = AGGREGATE.read_text(encoding="utf-8")
        assert "flags.digest_allowed(org)" in source
        assert "digests.ready_digest(" in source

    def test_the_ready_read_reads_one_day(self):
        assert digests.READY_FOR.days == 1
        assert "stored_at >= :since" in digests.READY_SQL
        assert "body IS NOT NULL" in digests.READY_SQL


# ── The fences of the table ─────────────────────────────────────────────────


_TABLE = re.compile(r"(?<![A-Za-z0-9_])crm_digests(?![A-Za-z0-9_])")


class TestTheTableNames:
    def test_only_the_writer_the_reader_and_the_reset_lists_name_the_table(self):
        named = set()
        for root in ("apps", "packages"):
            for path in (REPO / root).rglob("*.py"):
                if _TABLE.search(path.read_text(encoding="utf-8", errors="ignore")):
                    named.add(path.relative_to(REPO).as_posix())
        assert named == {
            "apps/services/gateway/gateway/crm_sync/digest.py",
            "apps/services/gateway/gateway/routes/crm/digests.py",
            "apps/services/gateway/gateway/routes/crm/core.py",
            "apps/services/gateway/gateway/routes/crm/settings.py",
        }

    def test_the_reset_lists_take_the_table(self):
        from gateway.routes.crm import settings as crm_settings

        assert "crm_digests" in core.RESET_TABLES
        assert "crm_digests" in crm_settings._RESET_DELETES

    def test_only_the_step_writes_it(self):
        source = ROUTE.read_text(encoding="utf-8")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", source)
