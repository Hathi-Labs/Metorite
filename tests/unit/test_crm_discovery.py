"""Discovery, the source profile, the edition budget and the deep link
(WS-53 CRM-Q1 and CRM-Q2, ``crm_platform.md`` §18).

Hermetic: every request goes to an ``httpx.MockTransport``. No database, no
network. ``test_crm_pipelines_r8.py`` holds the rules that need a real
database.

What is proved here:

* ``discover`` reads METADATA paths only, counts each call, and never lists a
  record. A section that fails (not fatal) is named in ``failed``. A dead
  token, a spent budget or a throttle stops the read.
* ``build_profile`` holds no record and no token, keeps the previous value of a
  section it could not read, and gives a stable hash.
* The budget follows the edition: Free 5,000 up to Ultimate with no cap.
* The deep link is built for each entity from the accounts host, the org
  ``domain_name`` and the tab, and it is ``None`` without a domain.
* A deal names its pipeline as a string or as an object.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from gateway.crm_sources import BudgetExhausted, NeedsReconnect, SourceError
from gateway.crm_sources.zoho import ZohoSource, links
from gateway.crm_sources.zoho import mapping as zm
from gateway.crm_sync import engine, profiles

from tests.unit.test_crm_sources_zoho import CONFIG, Fake, credential, ok

ORG = {
    "id": "9",
    "domain_name": "org123",
    "iso_currency": "INR",
    "time_zone": "Asia/Kolkata",
    "mc_status": False,
    "company_name": "SENTINEL-COMPANY-NAME",
    "license_details": {
        "paid": True, "paid_type": "professional", "users_license_purchased": 12,
    },
}

MODULES = {"modules": [
    {"api_name": "Deals", "module_name": "Potentials", "plural_label": "Deals",
     "singular_label": "Deal", "generated_type": "default", "api_supported": True,
     "visible": True},
    {"api_name": "Leads", "module_name": "Leads", "plural_label": "Leads",
     "generated_type": "default", "api_supported": True, "visible": True},
    {"api_name": "Installs", "module_name": "Installs", "plural_label": "Installs",
     "singular_label": "Install", "generated_type": "custom", "api_supported": True,
     "visible": True},
    {"api_name": "Quotes", "module_name": "Quotes", "plural_label": "Quotes",
     "generated_type": "default", "api_supported": False, "visible": True},
]}

PIPELINES = {"pipeline": [
    {"id": "p1", "display_value": "Standard", "default": True, "maps": [
        {"actual_value": "Qualify", "sequence_number": 1,
         "forecast_category": {"name": "Pipeline"}, "forecast_type": "Open"},
        {"actual_value": "Won", "sequence_number": 2,
         "forecast_category": {"name": "Closed Won"}, "forecast_type": "Closed Won"},
    ]},
    {"id": "p2", "display_value": "Renewals", "default": False, "maps": [
        {"actual_value": "Qualify", "sequence_number": 1},
        {"actual_value": "Lost", "sequence_number": 2, "forecast_type": "Closed Lost"},
    ]},
]}


def discovery_script(*, org: dict[str, Any] | None = ORG) -> list[Any]:
    """The answers of one discovery, in call order: org, modules, then a layouts
    and a fields read for each of four modules, then one pipeline read."""
    script: list[Any] = [ok({"org": [org]} if org else {}), ok(MODULES)]
    for module in ("Leads", "Contacts", "Accounts", "Deals"):
        layouts = [{"id": f"L-{module}", "name": "Standard", "status": "active",
                    "sections": [{"fields": [{"api_name": "x"}, {"api_name": "y"}]}]}]
        script.append(ok({"layouts": layouts}))
        script.append(ok({"fields": [
            {"api_name": "Region", "custom_field": True},
            {"api_name": "Name", "custom_field": False},
        ]}))
    script.append(ok(PIPELINES))
    return script


# ── discover ─────────────────────────────────────────────────────────────────


async def test_discover_reads_metadata_only_and_counts_each_call() -> None:
    fake = Fake(discovery_script())
    source = fake.source()
    raw = await source.discover()

    paths = [r.url.path for r in fake.requests]
    assert paths == [
        "/crm/v8/org", "/crm/v8/settings/modules",
        *[
            f"/crm/v8/settings/{kind}"
            for _module in range(4) for kind in ("layouts", "fields")
        ],
        "/crm/v8/settings/pipeline",
    ]
    # Metadata only: no path names a record module.
    assert not any(
        p.rstrip("/").split("/")[-1] in ("Leads", "Deals", "Contacts", "Accounts")
        for p in paths
    )
    assert source.credits_used == len(paths) == 11
    assert raw["failed"] == []
    assert raw["org"]["domain_name"] == "org123"
    assert [p["layout_id"] for p in raw["pipelines"]] == ["L-Deals", "L-Deals"]


async def test_a_section_that_fails_is_named_and_the_rest_goes_on() -> None:
    script = discovery_script()
    script[0] = ok({"code": "OAUTH_SCOPE_MISMATCH", "message": "scope"}, status=401)
    fake = Fake(script)
    raw = await fake.source().discover()
    assert raw["failed"] == ["org"]
    assert raw["org"] is None
    assert len(raw["modules"]) == 4 and len(raw["pipelines"]) == 2


async def test_a_spent_budget_stops_discovery() -> None:
    fake = Fake(discovery_script())
    with pytest.raises(BudgetExhausted):
        await fake.source(credit_budget=3).discover()
    assert len(fake.requests) == 3


async def test_a_dead_token_stops_discovery() -> None:
    def dead(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_code"})

    fake = Fake([dead])
    expired = credential(expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(NeedsReconnect):
        await fake.source(expired).discover()


# ── the profile ──────────────────────────────────────────────────────────────


def _profile(**raw_over: Any) -> dict[str, Any]:
    raw = {
        "org": ORG, "modules": MODULES["modules"],
        "layouts": {"Deals": [{"id": "L1", "name": "Standard", "sections": []}]},
        "fields": {"Deals": [{"api_name": "R", "custom_field": True}]},
        "pipelines": PIPELINES["pipeline"], "failed": [],
    }
    raw["pipelines"] = [{**p, "layout_id": "L1"} for p in raw["pipelines"]]
    raw.update(raw_over)
    return zm.build_profile(raw)


def test_the_profile_holds_no_record_no_token_and_no_company_name() -> None:
    text = json.dumps(_profile())
    assert "SENTINEL-COMPANY-NAME" not in text
    assert "token" not in text.lower()
    profile = _profile()
    assert profile["org"] == {
        "edition": "professional", "currency": "INR", "multi_currency": False,
        "time_zone": "Asia/Kolkata", "domain_name": "org123", "licences": 12,
    }
    by_name = {m["api_name"]: m for m in profile["modules"]}
    assert by_name["Installs"]["custom"] is True and by_name["Installs"]["mirrored"] is False
    assert by_name["Quotes"]["api_supported"] is False
    assert by_name["Deals"]["tab"] == "Potentials" and by_name["Deals"]["mirrored"] is True
    assert [p["name"] for p in profile["pipelines"]] == ["Standard", "Renewals"]
    assert [p["default"] for p in profile["pipelines"]] == [True, False]
    assert profile["fields"] == {"Deals": {"total": 1, "custom": 1}}


def test_the_hash_is_stable_and_moves_with_a_change() -> None:
    assert profiles.profile_hash(_profile()) == profiles.profile_hash(_profile())
    changed = _profile()
    changed["org"] = {**changed["org"], "edition": "enterprise"}
    assert profiles.profile_hash(changed) != profiles.profile_hash(_profile())


def test_a_failed_section_keeps_the_previous_value() -> None:
    before = _profile()
    after = zm.build_profile({"org": None, "failed": ["org"]}, before)
    assert after["org"] == before["org"]
    assert after["unreadable"] == ["org"]
    # With no previous, the failed section is empty and still named.
    assert zm.build_profile({"failed": ["org"]})["unreadable"] == ["org"]


def test_one_default_pipeline_even_when_zoho_marks_none_or_two() -> None:
    none = [{"id": "a", "display_value": "A"}, {"id": "b", "display_value": "B"}]
    assert [r["default"] for r in zm.pipeline_rows(none)] == [True, False]
    two = [
        {"id": "a", "display_value": "A", "default": True},
        {"id": "b", "display_value": "B", "default": True},
    ]
    assert [r["default"] for r in zm.pipeline_rows(two)] == [True, False]


def test_a_stage_the_same_in_two_pipelines_keeps_one_name() -> None:
    rows = zm.pipeline_rows(PIPELINES["pipeline"])
    assert [s["name"] for s in rows[0]["stages"]] == ["Qualify", "Won"]
    assert [s["name"] for s in rows[1]["stages"]] == ["Qualify", "Lost"]
    assert rows[0]["stages"][1]["type"] == "won" and rows[1]["stages"][1]["type"] == "lost"
    assert rows[0]["stages"][0]["forecast_category"] == "Pipeline"


# ── a deal names its pipeline ────────────────────────────────────────────────


def test_a_deal_pipeline_is_a_name_or_an_object() -> None:
    assert zm.pipeline_ref({"Pipeline": "Standard"}) == (None, "Standard")
    assert zm.pipeline_ref({"Pipeline": {"id": "p1", "name": "Standard"}}) == (
        "p1", "Standard")
    assert zm.pipeline_ref({"Pipeline": {"name": "Standard"}}) == (None, "Standard")
    assert zm.pipeline_ref({"Pipeline": None}) == (None, None)
    assert zm.pipeline_ref({}) == (None, None)
    assert zm.pipeline_ref({"Pipeline": "  "}) == (None, None)


def test_the_mapped_deal_carries_the_pipeline_and_other_entities_do_not() -> None:
    deal = zm.map_record(
        "deal", {"id": "1", "Deal_Name": "D", "Stage": "Qualify", "Pipeline": "Standard"},
        modified_at=None, owners={},
    )
    assert deal.pipeline == (None, "Standard")
    # The value stays in ``custom`` so a later discovery can re-link the deal.
    assert deal.values["custom"]["Pipeline"] == "Standard"
    lead = zm.map_record("lead", {"id": "2", "Last_Name": "L"}, modified_at=None, owners={})
    assert lead.pipeline is None


# ── the budget follows the edition ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("edition", "licences", "daily"),
    [
        ("free", 50, 5_000),
        ("standard", 10, 52_500),
        ("standard", 1_000, 100_000),
        ("professional", 12, 56_000),
        ("professional", 10_000, 3_000_000),
        ("enterprise", 100, 150_000),
        ("enterprise", 10_000, 5_000_000),
        ("ultimate", 100, 250_000),
        ("ultimate", 10_000, 20_050_000),
        ("professional", None, 50_000),
        ("crmplus", 5, None),
        (None, 5, None),
    ],
)
def test_daily_credits_by_edition(edition: str | None, licences: int | None,
                                  daily: int | None) -> None:
    assert zm.daily_credits(edition, licences) == daily


def _with(edition: str | None, licences: int | None = None) -> dict[str, Any]:
    return {"org": {"edition": edition, "licences": licences}}


def test_the_sync_budget_is_ten_percent_of_the_edition_and_500_when_unknown() -> None:
    assert engine.daily_budget(_with("free")) == 500
    assert engine.daily_budget(_with("professional", 12)) == 5_600
    assert engine.daily_budget(_with("enterprise", 10_000)) == 500_000
    # Unknown edition, no org section and no profile keep the CRM-Z5 floor.
    assert engine.daily_budget(_with(None)) == engine.DAILY_CREDIT_BUDGET == 500
    assert engine.daily_budget({}) == 500
    assert engine.daily_budget(None) == 500


def test_the_interval_stays_sane_for_any_budget() -> None:
    floor = engine.INTERVAL_SECONDS
    assert engine.next_interval(None) == floor
    assert engine.next_interval(0, 5_600) == floor
    # A big budget: a run that spends little waits the base interval.
    assert engine.next_interval(100, 500_000) == floor
    # The default budget spaces a run that spent 500 credits to the 24 h share.
    assert engine.next_interval(500) == engine.MAX_INTERVAL_SECONDS
    # A zero or negative budget cannot divide by zero.
    assert engine.next_interval(10, 0) <= engine.MAX_INTERVAL_SECONDS
    # A run that spends its whole 10 % share waits no longer than the cap.
    assert engine.next_interval(5_600, 5_600) <= engine.MAX_INTERVAL_SECONDS


# ── the deep link ────────────────────────────────────────────────────────────

ACCOUNTS = "https://accounts.zoho.in"


@pytest.mark.parametrize(
    ("entity", "tab"),
    [("lead", "Leads"), ("contact", "Contacts"), ("company", "Accounts"),
     ("deal", "Potentials")],
)
def test_the_deep_link_for_each_entity(entity: str, tab: str) -> None:
    assert links.deep_link(ACCOUNTS, "org123", entity, "555") == (
        f"https://crm.zoho.in/crm/org123/tab/{tab}/555")


def test_the_tab_comes_from_the_modules_metadata_first() -> None:
    assert links.deep_link(ACCOUNTS, "org123", "deal", "5", {"Deals": "Pot2"}) == (
        "https://crm.zoho.in/crm/org123/tab/Pot2/5")
    profile = _profile()
    assert zm.profile_tabs(profile)["Deals"] == "Potentials"


@pytest.mark.parametrize(
    ("server", "host"),
    [("https://accounts.zoho.com", "crm.zoho.com"),
     ("https://accounts.zoho.eu", "crm.zoho.eu"),
     ("https://accounts.zoho.com.au", "crm.zoho.com.au"),
     ("https://accounts.zohocloud.ca", "crm.zohocloud.ca")],
)
def test_the_crm_host_follows_the_data_centre(server: str, host: str) -> None:
    assert links.crm_host(server) == host


@pytest.mark.parametrize(
    "args",
    [
        (ACCOUNTS, None, "deal", "5"),
        (ACCOUNTS, "", "deal", "5"),
        (ACCOUNTS, "org123", "note", "5"),
        (ACCOUNTS, "org123", "deal", None),
        (ACCOUNTS, "org/../x", "deal", "5"),
        ("https://evil.example", "org123", "deal", "5"),
        ("http://accounts.zoho.in", "org123", "deal", "5"),
        (None, "org123", "deal", "5"),
    ],
)
def test_the_deep_link_is_none_without_every_part(args: tuple[Any, ...]) -> None:
    assert links.deep_link(*args) is None


async def test_the_adapter_deep_link_uses_the_credential_domain() -> None:
    from dataclasses import replace

    base = credential(accounts_server=ACCOUNTS)
    known = replace(base, provider_meta={**base.provider_meta, "domain_name": "org123"})
    assert ZohoSource(CONFIG, known).deep_link("deal", "9") == (
        "https://crm.zoho.in/crm/org123/tab/Potentials/9")
    with pytest.raises(SourceError):
        ZohoSource(CONFIG, base).deep_link("deal", "9")


def test_the_pipeline_tab_link() -> None:
    assert links.tab_link(ACCOUNTS, "org123", "deal") == (
        "https://crm.zoho.in/crm/org123/tab/Potentials")
    assert links.tab_link(ACCOUNTS, None, "deal") is None


def test_stage_types_keep_won_and_lost_and_the_first_pipeline_decides() -> None:
    """The type pass of the engine reads only ``won`` and ``lost``.

    A stage with no type, or with another word, is absent, so its row keeps
    its type. When two pipelines disagree, the first pipeline decides.
    """
    pipelines = [
        {"name": "Sales", "stages": [
            {"name": "Payment Complete", "type": "won"},
            {"name": "Re-Evaluate", "type": "lost"},
            {"name": "Discovery", "type": None},
            {"name": "Odd", "type": "open"},
        ]},
        {"name": "Renewals", "stages": [
            {"name": "Re-Evaluate", "type": "won"},
            {"name": "", "type": "won"},
            "not a stage",
        ]},
        "not a pipeline",
    ]
    assert engine.stage_types(pipelines) == {
        "Payment Complete": "won", "Re-Evaluate": "lost",
    }
    assert engine.stage_types([]) == {}
