"""The read tools say how old the mirror of a module is (WS-53 CRM-Q30c).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30c items 4 to 7.
Task 3868.

Hermetic. ``crm_tools._get`` is the one GET of the tools, so a fake of it
answers each route. ``_utc_now`` is the clock, and ``_run_org`` gives the org
of the run, as the run boundary does. ``test_crm_mirror_age.py`` proves the
route.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from acb_skills import crm_tools as T

from tests.unit._crm_fakes import crm_switch_on

_ORG = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_ID = "33333333-3333-3333-3333-333333333333"
_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _age(key: str, *, ago: timedelta | None = None, held: bool = False,
         unavailable: bool = False) -> dict[str, Any]:
    return {
        "key": key, "api_name": key.removeprefix("module:"),
        "checked_at": (_NOW - ago).isoformat() if ago is not None else None,
        "held": held, "unavailable": unavailable,
    }


def _routes(ages: Any) -> dict[str, Any]:
    return {
        T.SHORT_READS_PATH: [],
        T.MIRROR_AGE_PATH: ages,
        "/crm/aggregate": {"groups": [{"key": "a", "label": "a", "value": 3}], "total": 3,
                           "zone": "UTC"},
        "/crm/modules": [{"api_name": "Invoices", "label": "Invoices", "count": 3}],
        "/crm/catalogue": T._GatewayError("off", "catalogue_off", status=404),
        "/crm/modules/Invoices/records": {
            "rows": [{"id": _ID, "name": "INV-1"}], "total": 1, "field_defs": []},
        "/crm/deals": {"rows": [{"id": _ID, "name": "Big deal"}], "total": 1},
    }


def _fake(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    async def get(path: str, params: dict[str, Any] | None = None, **_kw: Any) -> Any:
        calls.append(path)
        answer = routes[path]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(T, "_get", get)
    monkeypatch.setattr(T, "_utc_now", lambda: _NOW)
    return calls


def _on(monkeypatch: pytest.MonkeyPatch, listed: str = _ORG, run_org: str | None = _ORG) -> None:
    """The flag lists *listed*, and the run belongs to *run_org*."""
    from acb_common import get_settings

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_mirror_age_orgs", listed, raising=False)
    monkeypatch.setattr(T, "_run_org", lambda: run_org)


TOOLS = {
    "crm_aggregate": lambda: T.crm_aggregate("module:Invoices", "owner"),
    "crm_list_module_records": lambda: T.crm_list_module_records("Invoices"),
}


# ── Each state says its sentence ─────────────────────────────────────────────


STATES = {
    "three_hours": (_age("module:Invoices", ago=timedelta(hours=3)),
                    "Invoices were last read from Zoho 3 hours ago."),
    "three_days": (_age("module:Invoices", ago=timedelta(days=3)),
                   "Invoices were last read from Zoho 3 days ago."),
    "held": (_age("module:Invoices", ago=timedelta(minutes=5), held=True),
             "Invoices are held, so they may be old."),
    "unavailable": (_age("module:Invoices", unavailable=True),
                    "Invoices are not available in Zoho now, so they may be old."),
    "never_read": (_age("module:Invoices"),
                   "Invoices have no finished read from Zoho on record, so they may be old "
                   "or partial."),
}


@pytest.mark.parametrize("tool", sorted(TOOLS))
@pytest.mark.parametrize("state", sorted(STATES))
async def test_each_state_says_its_sentence(tool: str, state: str, monkeypatch) -> None:
    row, sentence = STATES[state]
    _on(monkeypatch)
    calls = _fake(monkeypatch, _routes([row]))
    out = await TOOLS[tool]()
    assert out.endswith("\n" + sentence)
    assert calls.count(T.MIRROR_AGE_PATH) == 1, "one read of the route for each call"


@pytest.mark.parametrize(("ago", "words"), [
    (timedelta(hours=2), "2 hours"),
    (timedelta(hours=47, minutes=59), "47 hours"),
    (timedelta(hours=48), "2 days"),
    (timedelta(days=9, hours=23), "9 days"),
])
async def test_the_age_words(ago: timedelta, words: str, monkeypatch) -> None:
    _on(monkeypatch)
    _fake(monkeypatch, _routes([_age("module:Invoices", ago=ago)]))
    out = await T.crm_list_module_records("Invoices")
    assert f"Invoices were last read from Zoho {words} ago." in out


@pytest.mark.parametrize("ago", [timedelta(0), timedelta(hours=1, minutes=59),
                                 timedelta(minutes=-5)])
async def test_a_fresh_module_says_nothing(ago: timedelta, monkeypatch) -> None:
    _on(monkeypatch)
    _fake(monkeypatch, _routes([_age("module:Invoices", ago=ago)]))
    out = await T.crm_list_module_records("Invoices")
    assert "Zoho" not in out


async def test_unavailable_wins_over_held(monkeypatch) -> None:
    _on(monkeypatch)
    _fake(monkeypatch, _routes([_age("module:Invoices", held=True, unavailable=True)]))
    out = await T.crm_list_module_records("Invoices")
    assert "Invoices are not available in Zoho now" in out
    assert "are held" not in out


# ── Flag off, and the module of the call ─────────────────────────────────────


@pytest.mark.parametrize(("listed", "run_org"), [
    ("", _ORG),          # the list is empty
    (_OTHER, _ORG),      # the list names another org
    ("*", None),         # the run has no org
])
async def test_flag_off_says_nothing_and_sends_nothing(listed: str, run_org: str | None,
                                                       monkeypatch) -> None:
    _on(monkeypatch, listed=listed, run_org=run_org)
    calls = _fake(monkeypatch, _routes([_age("module:Invoices", ago=timedelta(days=3))]))
    out = await T.crm_list_module_records("Invoices")
    assert T.MIRROR_AGE_PATH not in calls
    assert "Zoho" not in out


async def test_the_line_names_the_module_that_the_call_read(monkeypatch) -> None:
    _on(monkeypatch)
    ages = [
        _age("deals", ago=timedelta(hours=5)),
        _age("module:Invoices", ago=timedelta(days=3)),
        _age("module:Quotes", held=True),
    ]
    _fake(monkeypatch, _routes(ages))
    module = await T.crm_list_module_records("Invoices")
    assert "Invoices were last read from Zoho 3 days ago." in module
    assert "Quotes" not in module and "Deals" not in module
    core = await T.crm_list_records("deals")
    assert core.endswith("\nDeals were last read from Zoho 5 hours ago.")
    assert "Invoices" not in core and "Quotes" not in core


async def test_the_module_list_sends_no_age_read(monkeypatch) -> None:
    """``crm_list_modules`` reads no one module, so it adds no age line."""
    _on(monkeypatch)
    calls = _fake(monkeypatch, _routes([_age("module:Invoices", ago=timedelta(days=3))]))
    out = await T.crm_list_modules()
    assert T.MIRROR_AGE_PATH not in calls
    assert "last read" not in out


async def test_the_short_read_line_comes_first(monkeypatch) -> None:
    _on(monkeypatch)
    routes = _routes([_age("module:Invoices", held=True)])
    routes[T.SHORT_READS_PATH] = [
        {"key": "module:Invoices", "zoho_count": 10, "readable": 8, "short_by": 2},
    ]
    _fake(monkeypatch, routes)
    out = await T.crm_list_module_records("Invoices")
    short = out.index("Note: Zoho holds 10 Invoices records")
    held = out.index("Invoices are held, so they may be old.")
    assert short < held


@pytest.mark.parametrize("failure", [
    RuntimeError("CRM GET /crm/sync/mirror-age failed (500)"),
    T._Refused(T.CRM_CLOSED),
])
async def test_a_failed_read_adds_nothing(failure: Exception, monkeypatch) -> None:
    _on(monkeypatch)
    _fake(monkeypatch, _routes(failure))
    out = await T.crm_list_module_records("Invoices")
    assert out.startswith("Invoices: 1 records")
    assert "Zoho" not in out and T.CRM_CLOSED not in out


@pytest.mark.parametrize("ages", [
    [{"key": "module:Invoices", "checked_at": "not a time", "held": False}],
    [{"checked_at": None}, "x", None],
    {"module:Invoices": "held"},
])
async def test_a_row_of_a_wrong_shape_adds_nothing(ages: Any, monkeypatch) -> None:
    _on(monkeypatch)
    _fake(monkeypatch, _routes(ages))
    assert "Zoho" not in await T.crm_list_module_records("Invoices")


def test_one_helper_writes_each_sentence() -> None:
    """Each sentence lives in ``_age_line`` only, and ``_short_note`` is the
    one caller of ``_age_lines``. A second copy would drift."""
    source = inspect.getsource(T)
    for words in ("were last read from Zoho", "are held, so they may be old",
                  "are not available in Zoho now", "have no finished read from Zoho"):
        assert source.count(words) == 1, words
    assert source.count("await _age_lines(") == 1
    assert "await _age_lines(" in inspect.getsource(T._short_note)


def test_mirror_age_on_asks_the_one_reader(monkeypatch) -> None:
    """The tool reads no setting itself. It asks the gateway's reader."""
    seen: list[Any] = []

    def reader(org: Any) -> bool:
        seen.append(org)
        return True

    from gateway.routes.crm import flags

    monkeypatch.setattr(flags, "mirror_age_allowed", reader)
    monkeypatch.setattr(T, "_run_org", lambda: _ORG)
    assert T.mirror_age_on() is True
    assert seen == [_ORG]
    assert "crm_mirror_age_orgs" not in inspect.getsource(T)
