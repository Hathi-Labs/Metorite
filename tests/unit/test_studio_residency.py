"""STU-J10: data residency for each org. Spec: ai_studio.md section 14 J10.

Acceptance: a test lists each outbound path with its region and refuses a path
with no region. With the flag on, no record goes outside India. An org without
the flag sees no change.
"""
from __future__ import annotations

import pytest
from acb_common import studio_residency as r
from acb_common.settings import Settings


@pytest.fixture
def flag(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(r, "get_settings", lambda: Settings(studio_residency_orgs=value))
    return _set


def test_every_outbound_path_has_a_region():
    assert r.HEALTH_OUTBOUND_PATHS
    assert r.validate_registry() == []


def test_the_registry_refuses_a_path_with_no_region():
    bad = (r.OutboundPath("x", "vendor", None, True, "src"),)
    assert r.validate_registry(bad) == ["x: no region"]
    assert r.validate_registry((r.OutboundPath("x", "vendor", "mars", True, "s"),))


def test_the_registry_refuses_a_repeated_key_and_a_missing_source():
    p = r.OutboundPath("x", "store", r.REGION_IN, True, " ")
    out = r.validate_registry((p, p))
    assert any("twice" in m for m in out) and any("source" in m for m in out)


def test_flag_off_changes_nothing(flag):
    flag("")
    for p in r.HEALTH_OUTBOUND_PATHS:
        assert r.path_allowed("org-a", p.key)
    r.require_path("org-a", "llm_anthropic")


def test_flag_on_blocks_every_path_outside_india(flag):
    flag("org-a")
    for p in r.HEALTH_OUTBOUND_PATHS:
        assert r.path_allowed("org-a", p.key) is (p.region == r.REGION_IN)
    with pytest.raises(r.ResidencyRefusal):
        r.require_path("org-a", "llm_anthropic")
    r.require_path("org-a", "postgres")


def test_flag_on_fails_closed_on_an_unregistered_path(flag):
    flag("org-a")
    assert not r.path_allowed("org-a", "never_registered")


def test_the_flag_is_per_org_and_star_opens_all(flag):
    flag("org-a")
    assert r.path_allowed("org-b", "llm_anthropic")
    flag("*")
    assert not r.path_allowed("org-b", "llm_anthropic")
    assert r.residency_on(None)


def test_listing_is_case_and_space_tolerant(flag):
    flag(" ORG-A , org-c")
    assert r.residency_on("Org-A") and r.residency_on("org-c")


def test_no_org_fails_closed_when_any_org_holds_the_flag(flag):
    flag("")
    assert r.path_allowed(None, "llm_anthropic")
    flag("org-a")
    assert not r.path_allowed(None, "llm_anthropic")


def test_the_object_store_is_not_claimed_as_india():
    assert r._BY_KEY["object_store"].region != r.REGION_IN


def test_each_vendor_secret_is_covered():
    """Every api key and token of Settings has a registry row or is listed as no record path."""
    import re

    from acb_common.settings import Settings

    secrets = {n for n in Settings.model_fields
               if re.search(r"(api_key|api_token|access_token|refresh_token|_token)$", n)}
    unknown = sorted(secrets - set(r.VENDOR_SETTING_KEYS) - r.NOT_A_RECORD_PATH)
    assert unknown == [], f"add a registry row or list as no record path: {unknown}"
    for name, key in r.VENDOR_SETTING_KEYS.items():
        assert name in Settings.model_fields, name
        assert key in r._BY_KEY, key


def test_unknown_region_is_refused_with_the_flag_on(flag):
    flag("org-a")
    assert not r.path_allowed("org-a", "object_store")
    assert not r.path_allowed("org-a", "redis")
    assert r.path_allowed("org-a", "postgres")


def test_a_store_that_holds_records_says_so():
    assert r._BY_KEY["redis"].carries_records is True
