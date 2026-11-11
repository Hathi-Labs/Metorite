"""STU-G20: the Studio API contract version, and the golden manifest schema.

R7 mutation note: change ``<`` to ``<=`` in the retired test, or ``WARN_DAYS``
to 29, or drop the type check, and a test below fails. Edit the live
``studio_manifest.schema.json`` and the golden test fails.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from acb_common import studio_contract as sc
from acb_common.studio_contract import ContractVersion, check_contract

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "packages/acb_common/acb_common/studio_manifest.schema.json"
GOLDEN = ROOT / "tests/unit/golden/studio_manifest.schema.json"

RETIRE = date(2027, 6, 30)
TABLE = (
    ContractVersion("1", date(2026, 11, 11), RETIRE),
    ContractVersion("2", date(2027, 1, 1)),
)


@pytest.fixture
def table():
    with patch.object(sc, "CONTRACT_VERSIONS", TABLE):
        yield


def test_shipped_version_one_is_ok():
    r = check_contract("1", date(2026, 11, 11))
    assert (r.status, r.code) == ("ok", None)


def test_unknown_version_is_refused_with_the_fixed_code():
    r = check_contract("99", date(2026, 11, 12))
    assert r.status == "refused"
    assert r.code == "contract_unsupported"


@pytest.mark.parametrize(
    "bad",
    [None, 1, 1.0, [], {}, b"1", "", " 1", "1 ", "v1", "1.0", "-1", "1\n", "\u0661", "9" * 50],
)
def test_bad_input_is_refused_and_never_raises(bad):
    r = check_contract(bad, date(2026, 12, 1))
    assert r.status == "refused"
    assert r.code == "contract_unsupported"


def test_version_before_served_from_is_refused():
    assert check_contract("1", date(2026, 11, 10)).status == "refused"


def test_far_from_retire_is_ok(table):
    r = check_contract("1", RETIRE - timedelta(days=31))
    assert (r.status, r.days_left) == ("ok", 31)


def test_exactly_30_days_warns(table):
    r = check_contract("1", RETIRE - timedelta(days=30))
    assert (r.status, r.days_left) == ("warn", 30)
    assert "30 days" in r.message


def test_retire_day_itself_still_serves_with_a_warning(table):
    r = check_contract("1", RETIRE)
    assert (r.status, r.days_left) == ("warn", 0)


def test_day_after_retire_is_refused(table):
    r = check_contract("1", RETIRE + timedelta(days=1))
    assert (r.status, r.code) == ("refused", "contract_unsupported")


def test_version_with_no_retire_date_never_warns(table):
    assert check_contract("2", date(2030, 1, 1)).status == "ok"


def test_support_window_is_publishable_json(table):
    w = sc.support_window()
    json.dumps(w)
    assert w["warn_days"] == 30
    assert w["versions"][0] == {
        "version": "1",
        "served_from": "2026-11-11",
        "retires_on": "2027-06-30",
    }
    assert w["versions"][1]["retires_on"] is None


def test_current_version_is_in_the_table():
    assert sc.CURRENT_VERSION in {r.version for r in sc.CONTRACT_VERSIONS}


def test_live_manifest_schema_equals_the_golden_file():
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert live == golden, (
        "The manifest schema changed, and the kit may break. This needs a decision on the "
        "Studio API contract version in acb_common/studio_contract.py. If the change is on "
        "purpose, copy packages/acb_common/acb_common/studio_manifest.schema.json to "
        "tests/unit/golden/studio_manifest.schema.json in the same change."
    )
