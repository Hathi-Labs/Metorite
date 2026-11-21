"""The finding contract (LOOP-7, ``proactive_loop.md`` §6 and §8.4).

Pure tests. The delivery check against real group rows is in
``test_finding_contract_r8.py``.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
from gateway.routes.shell import finding_contract as fc


def _finding(**over):
    base = dict(
        kind="metric_moved", entity={"type": "metric", "id": "m1", "label": "Revenue"},
        signal_id="metrics:m1", fingerprint="abc", facts={"delta": 12},
        expires_at=None, acts=(), href="/metrics/m1",
        evidence=(fc.Evidence("crm", "deal", "d1", "Deal", "/crm/d1"),),
        confidence=1.0, visibility_scope=("org",), severity_hint="info",
        batch_key="metrics", organization_id="org-a", app="metrics", title="Revenue",
    )
    base.update(over)
    return fc.Finding(**base)


def _decl(**over):
    base = dict(
        kind="metric_moved", label="A metric moved", point="insights", group="info",
        why=("metric_moved.why",), source="metrics.provider", entity_types=("metric",),
        reach_class="brief",
        fixtures={"positive": _finding(), "negative": _finding(evidence=())},
    )
    base.update(over)
    return fc.KindDeclaration(**base)


def test_a_good_declaration_and_its_fixtures_pass():
    d = _decl()
    assert fc.check_declaration(d) == []
    assert fc.check_fixtures(d) == []


@pytest.mark.parametrize("over,word", [
    ({"fixtures": {}}, "fixture"),
    ({"point": "bogus"}, "point"),
    ({"group": "now"}, "group"),  # a nudge group on an insights kind
    ({"reach_class": "urgent"}, "brief at most"),
    ({"reach_class": "loud"}, "reach_class"),
    ({"harm_class": "x"}, "harm_class"),
    ({"prior": 21}, "prior"),
    ({"lower_after": 0}, "lower_after"),
    ({"why": ()}, "why"),
    ({"entity_types": ("person",)}, "person"),
    ({"entity_types": ("Person",)}, "person"),
    ({"entity_types": ("member",)}, "unknown type"),
])
def test_a_bad_declaration_is_refused(over, word):
    faults = fc.check_declaration(_decl(**over))
    assert any(word in f for f in faults), faults


def test_a_nudge_group_fits_a_nudges_kind():
    d = _decl(point="nudges", group="needs_you", reach_class="interrupt",
              fixtures={"positive": _finding(severity_hint="needs_you"),
                        "negative": _finding(evidence=())})
    assert fc.check_declaration(d) == []
    assert fc.check_fixtures(d) == []


def test_the_default_reach_class_is_none():
    assert dataclasses.replace(_decl(), reach_class="none").reach_class == "none"
    d = fc.KindDeclaration(kind="k", label="l", point="nudges", group="now",
                           why=("w",), source="s", entity_types=("task",))
    assert d.reach_class == "none" and d.lower_after == 3 and d.harm_class == "normal"


@pytest.mark.parametrize("over,word", [
    ({"evidence": ()}, "evidence"),
    ({"evidence": tuple(fc.Evidence("a", "t", str(i), "l", "/h") for i in range(4))}, "evidence"),
    ({"visibility_scope": ()}, "visibility_scope"),
    ({"visibility_scope": ("everyone",)}, "bad subject"),
    ({"confidence": 1.5}, "confidence"),
    ({"severity_hint": "loud"}, "severity_hint"),
    ({"entity": {"type": "person", "id": "p", "label": "Ben"}}, "person"),
    ({"entity": {"type": "Person", "id": "p", "label": "Ben"}}, "not allowed"),
    ({"entity": {"type": " person", "id": "p", "label": "Ben"}}, "not allowed"),
    ({"entity": {"type": "member", "id": "p", "label": "Ben"}}, "not allowed"),
    ({"entity": {"type": "user", "id": "p", "label": "Ben"}}, "not allowed"),
    ({"fingerprint": ""}, "fingerprint"),
])
def test_a_bad_finding_drops(over, word):
    faults = fc.check_finding(_finding(**over))
    assert any(word in f for f in faults), faults


def test_a_finding_is_held_to_its_kind():
    d = _decl()
    assert fc.check_finding(_finding(), d) == []
    assert fc.check_finding(_finding(kind="other"), d)
    assert fc.check_finding(_finding(entity={"type": "task", "id": "1", "label": "T"}), d)
    assert fc.check_finding(_finding(severity_hint="now"), d)


def test_a_passing_negative_fixture_is_refused():
    d = _decl(fixtures={"positive": _finding(), "negative": _finding()})
    assert any("tests nothing" in f for f in fc.check_fixtures(d))


def test_a_failing_positive_fixture_is_refused():
    d = _decl(fixtures={"positive": _finding(evidence=()), "negative": _finding(evidence=())})
    assert any("positive" in f for f in fc.check_fixtures(d))


def test_the_scope_holds_the_member_the_group_and_the_org():
    assert fc.scope_holds(["org"], "a@x.test", [])
    assert fc.scope_holds(["A@X.test"], "a@x.test", [])
    assert fc.scope_holds(["group:sales"], "a@x.test", ["group:Sales"])
    assert not fc.scope_holds(["group:sales"], "a@x.test", ["group:ops"])
    assert not fc.scope_holds(["b@x.test"], "a@x.test", ["group:sales"])


def test_the_scope_fails_closed():
    assert not fc.scope_holds([], "a@x.test", [])
    assert not fc.scope_holds(["org"], "", [])
    assert not fc.scope_holds(["org"], None, [])  # type: ignore[arg-type]


def test_delivery_drops_in_silence_and_checks_the_contract_again():
    d = _decl()
    reg = {d.kind: d}
    mine = _finding(visibility_scope=("a@x.test",))
    theirs = _finding(visibility_scope=("b@x.test",), signal_id="metrics:m2")
    broken = _finding(evidence=(), signal_id="metrics:m3")
    out = fc.deliver([mine, theirs, broken], "a@x.test", [], organization_id="org-a", registry=reg)
    assert out == [mine]


def test_delivery_drops_a_recipient_of_another_tenant_even_for_org_scope():
    d = _decl()
    f = _finding(visibility_scope=("org",))
    assert fc.deliver([f], "a@x.test", [], organization_id="org-a", registry={d.kind: d}) == [f]
    assert fc.deliver([f], "a@x.test", [], organization_id="org-b", registry={d.kind: d}) == []
    assert fc.deliver([f], "a@x.test", [], organization_id="", registry={d.kind: d}) == []
    unowned = _finding(organization_id="")
    assert fc.deliver([unowned], "a@x.test", [], organization_id="", registry={d.kind: d}) == []


def test_delivery_drops_an_undeclared_kind_and_uses_the_module_registry_by_default(monkeypatch):
    d = _decl()
    f = _finding()
    assert fc.deliver([f], "a@x.test", [], organization_id="org-a", registry={}) == []
    monkeypatch.setattr(fc, "REGISTRY", {})
    assert fc.deliver([f], "a@x.test", [], organization_id="org-a") == []
    monkeypatch.setattr(fc, "REGISTRY", {d.kind: d})
    assert fc.deliver([f], "a@x.test", [], organization_id="org-a") == [f]


def test_delivery_drops_an_expired_finding_and_one_with_an_unreadable_expiry():
    d = _decl()
    reg = {d.kind: d}
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    live = _finding(expires_at="2026-11-17T00:00:00+00:00")
    old = _finding(expires_at="2026-11-16T11:00:00+00:00", signal_id="metrics:m2")
    junk = _finding(expires_at="soon", signal_id="metrics:m3")
    naive = _finding(expires_at="2026-11-17T00:00:00", signal_id="metrics:m4")
    out = fc.deliver([live, old, junk, naive], "a@x.test", [], organization_id="org-a",
                     registry=reg, now=now)
    assert out == [live]


def test_a_finding_gives_the_signal_that_the_feed_reads():
    sig = _finding().to_signal()
    assert (sig.signal_id, sig.app, sig.kind, sig.title) == ("metrics:m1", "metrics", "metric_moved", "Revenue")


def test_register_refuses_a_kind_with_no_fixture_and_a_duplicate(monkeypatch):
    monkeypatch.setattr(fc, "REGISTRY", {})
    with pytest.raises(ValueError, match="fixture"):
        fc.register(_decl(fixtures={}))
    fc.register(_decl())
    with pytest.raises(ValueError, match="already declared"):
        fc.register(_decl())


def test_every_declared_kind_passes_its_own_fixtures():
    """The §6.5 fence: a new kind with no fixture fails here."""
    for name, d in fc.REGISTRY.items():
        assert fc.check_declaration(d) == [], name
        assert fc.check_fixtures(d) == [], name
