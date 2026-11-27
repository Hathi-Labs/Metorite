"""The shadow gate of a new nudge kind (LOOP-9, ``proactive_loop.md`` §7.6).

Each rule names the mutation that turns it red (R7):

1. a kind in ``shadow`` or ``off`` that reaches a member ->
   ``test_only_on_reaches_a_member``.
2. a doubt that does not give ``off`` -> ``test_every_doubt_is_off``.
3. a shadow list that skips the delivery checks ->
   ``test_shadowed_runs_the_same_checks_as_delivery``.
4. a match outside 24 hours, before the finding, or across member or org ->
   ``test_precision_counts_one_member_one_org_inside_the_window``.
5. a small pool that shows a number -> ``test_fewer_than_five_members_is_too_few``.
6. a gate that passes with no number or no measure ->
   ``test_the_gate_refuses_every_doubt``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from acb_common import get_settings
from gateway.routes.shell import finding_contract as fc
from gateway.routes.shell import finding_shadow as fs

T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def _finding(org="org-a", scope=("org",), eid="m1"):
    return fc.Finding(
        kind="metric_moved", entity={"type": "metric", "id": eid, "label": "Revenue"},
        signal_id=f"metrics:{eid}", fingerprint="abc", facts={"delta": 1},
        expires_at=None, acts=(), href="/m", evidence=(fc.Evidence("crm", "deal", "d", "D", "/d"),),
        visibility_scope=scope, severity_hint="info", organization_id=org, app="metrics")


def _decl(**over):
    base = dict(kind="metric_moved", label="A metric moved", point="insights", group="info",
                why=("w",), source="metrics.provider", entity_types=("metric",),
                reach_class="brief", fixtures={"positive": _finding(),
                                               "negative": _finding(scope=())})
    base.update(over)
    return fc.KindDeclaration(**base)


def _mode(monkeypatch, modes, orgs="org-a"):
    monkeypatch.setattr(get_settings(), "proactive_kind_modes", modes, raising=False)
    monkeypatch.setattr(get_settings(), "proactive_kind_orgs", orgs, raising=False)


def _deliver(f, org="org-a", fn=fc.deliver):
    return fn([f], "a@x.test", [], organization_id=org, registry={"metric_moved": _decl()})


def test_only_on_reaches_a_member(monkeypatch):
    f = _finding()
    for modes, got_live, got_shadow in (("metric_moved=on", [f], []),
                                        ("metric_moved=shadow", [], [f]),
                                        ("metric_moved=off", [], [])):
        _mode(monkeypatch, modes)
        assert _deliver(f) == got_live
        assert _deliver(f, fn=fc.shadowed) == got_shadow


@pytest.mark.parametrize("modes,orgs,org", [
    ("", "*", "org-a"), ("metric_moved=on", "", "org-a"), ("metric_moved=on", "org-b", "org-a"),
    ("metric_moved=loud", "*", "org-a"), ("other=on", "*", "org-a"),
    ("metric_moved=on", "*", ""), ("metric_moved=on,metric_moved=off", "*", "org-a"),
])
def test_every_doubt_is_off(monkeypatch, modes, orgs, org):
    _mode(monkeypatch, modes, orgs)
    assert fc.kind_mode("metric_moved", org) == "off"


def test_the_star_and_the_list_allow_an_org(monkeypatch):
    _mode(monkeypatch, "metric_moved=shadow", "*")
    assert fc.kind_mode("metric_moved", "org-z") == "shadow"
    _mode(monkeypatch, " metric_moved = ON ", "org-b, org-a")
    assert fc.kind_mode("metric_moved", "org-a") == "on"


def test_shadowed_runs_the_same_checks_as_delivery(monkeypatch):
    _mode(monkeypatch, "metric_moved=shadow")
    assert _deliver(_finding(org="org-b"), fn=fc.shadowed) == []  # another tenant
    assert _deliver(_finding(scope=("kiran@x.test",)), fn=fc.shadowed) == []  # outside the scope
    assert fc.shadowed([_finding()], "a@x.test", [], organization_id="org-a", registry={}) == []


def _shown(member="a@x.test", org="org-a", eid="m1", at=T0, kind="metric_moved"):
    return fs.Shown(kind, org, member, "metric", eid, at)


def _act(member="a@x.test", org="org-a", eid="m1", at=T0 + timedelta(hours=1)):
    return fs.Act(org, member, "metric", eid, at)


def _five(**over):
    return [_shown(member=f"m{i}@x.test", **over) for i in range(5)]


def test_precision_counts_one_member_one_org_inside_the_window():
    shown = _five()
    acts = [
        _act(member="m0@x.test"),                                  # hit
        _act(member="m1@x.test", at=T0 + timedelta(hours=24)),     # hit at the edge
        _act(member="m2@x.test", at=T0 + timedelta(hours=25)),     # too late
        _act(member="m3@x.test", at=T0 - timedelta(minutes=1)),    # before the finding
        _act(member="m4@x.test", org="org-b"),                     # another org
        _act(member="m0@x.test", eid="other"),                     # another record
        _act(member="stranger@x.test"),                            # another member
    ]
    row = fs.precision(shown, acts)["metric_moved"]["org-a"]
    assert (row["shown"], row["acted"], row["members"]) == (5, 2, 5)
    assert row["precision"] == 0.4 and row["too_few"] is False


def test_two_acts_on_one_finding_count_once_and_the_answer_names_no_member():
    shown = _five()
    acts = [_act(member="m0@x.test"), _act(member="m0@x.test", at=T0 + timedelta(hours=2))]
    out = fs.precision(shown, acts)
    assert out["metric_moved"]["org-a"]["acted"] == 1
    assert "m0@x.test" not in repr(out)


def test_fewer_than_five_members_is_too_few():
    row = fs.precision(_five()[:4], [_act(member="m0@x.test")])["metric_moved"]["org-a"]
    assert row["too_few"] is True and row["precision"] is None and row["shown"] == 4


def test_each_org_and_kind_has_its_own_row():
    out = fs.precision(_five(org="org-a") + _five(org="org-b", kind="other"), [])
    assert set(out) == {"metric_moved", "other"}
    assert set(out["metric_moved"]) == {"org-a"} and set(out["other"]) == {"org-b"}


def test_the_gate_refuses_every_doubt():
    good = {"precision": 0.5, "too_few": False}
    assert fs.gate_passes(_decl(gate=0.4), good) is True
    assert fs.gate_passes(_decl(gate=0.5), good) is True
    assert fs.gate_passes(_decl(gate=0.6), good) is False
    assert fs.gate_passes(_decl(), good) is False                       # no number written
    assert fs.gate_passes(_decl(gate=0.1), {"precision": None, "too_few": True}) is False
    assert fs.gate_passes(_decl(gate=0.1), {"precision": 0.9, "too_few": True}) is False
    assert fs.gate_passes(_decl(gate=0.1), {}) is False


@pytest.mark.parametrize("gate", [0, -0.1, 1.5, True, "0.5"])
def test_a_bad_gate_number_is_refused(gate):
    assert any("gate" in x for x in fc.check_declaration(_decl(gate=gate)))
    assert not any("gate" in x for x in fc.check_declaration(_decl(gate=0.7)))


def test_shown_of_keeps_the_typed_entity_and_lowers_the_member():
    s = fs.shown_of(_finding(), " A@X.test ", T0)
    assert (s.member, s.entity_type, s.entity_id, s.organization_id) == (
        "a@x.test", "metric", "m1", "org-a")
