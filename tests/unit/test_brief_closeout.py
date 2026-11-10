"""WS-BRIEF BR-2b-1, the pure parts of the close-out (no database).

Spec: ``project-docs/specs/core_brief.md`` §4.3 and acceptance row 7a.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from gateway.brief import flags, gather, planner
from gateway.brief import run as brief_run
from gateway.routes.shell.handled import HandledRun
from gateway.routes.shell.needs import Closed

ORG = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2099, 3, 10, 13, 0, tzinfo=UTC)
D = date(2099, 3, 10)


def _settings(monkeypatch, **kw) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**kw))


def _closed(key="tasks:42", title="Ship it", href="/tasks?x=1") -> Closed:
    return Closed(key=key, app=key.split(":")[0], title=title, href=href, by={}, at=None)


def test_the_switch_is_dark_by_default_and_a_platform_env() -> None:
    from acb_common.env_guard import is_platform_env
    from acb_common.settings import Settings

    assert Settings.model_fields["brief_closeout"].default is False
    assert is_platform_env("BRIEF_CLOSEOUT")


def test_closeout_needs_the_switch_and_the_org(monkeypatch) -> None:
    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG)
    assert flags.closeout(ORG) is False
    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG, brief_closeout=True)
    assert flags.closeout(ORG) is True
    assert flags.closeout("22222222-2222-2222-2222-222222222222") is False
    _settings(monkeypatch, brief_enabled=False, brief_orgs=ORG, brief_closeout=True)
    assert flags.closeout(ORG) is False
    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG, brief_closeout="true")
    assert flags.closeout(ORG) is False


def test_kinds_of_follows_the_switch(monkeypatch) -> None:
    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG)
    assert planner.kinds_of(ORG) == ("brief",)
    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG, brief_closeout=True)
    assert planner.kinds_of(ORG) == ("brief", "closeout")


def test_a_payload_with_no_kind_is_a_brief_and_a_bad_kind_raises() -> None:
    assert brief_run.kind_of({}) == "brief"
    assert brief_run.kind_of({"kind": "closeout"}) == "closeout"
    with pytest.raises(ValueError):
        brief_run.kind_of({"kind": "weekly"})


def test_a_brief_payload_names_no_kind_and_a_closeout_does() -> None:
    m = planner.Member("a@x.test", True, time(7, 30), time(18, 30))
    at = datetime(2099, 3, 10, 7, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert "kind" not in planner.payload_of(planner.Slot(m, D, at))
    assert planner.payload_of(planner.Slot(m, D, at, kind="closeout"))["kind"] == "closeout"
    assert planner.dedupe_key("a@x.test", "closeout", D) == "brief:a@x.test:closeout:2099-03-10"


def test_why_not_reads_the_closeout_time_for_a_closeout() -> None:
    prefs = {"enabled": True, "brief_time": "07:30", "closeout_time": "18:30"}
    slot = datetime(2099, 3, 10, 18, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert brief_run._why_not(prefs, slot, "closeout") is None
    assert brief_run._why_not(prefs, slot, "brief") == brief_run.MOVED
    assert brief_run._why_not({**prefs, "closeout_time": "19:00"}, slot, "closeout") == "moved"


def test_a_closeout_never_composes(monkeypatch) -> None:
    from acb_llm import routed

    _settings(monkeypatch, brief_enabled=True, brief_orgs=ORG, brief_compose=True)
    monkeypatch.setattr(routed, "routing_is_on", lambda: True)
    assert brief_run._composes(ORG, 4, "brief") is True
    assert brief_run._composes(ORG, 4, "closeout") is False


def test_handled_run_carries_the_closed_rows_and_the_answer_keeps_its_shape() -> None:
    assert HandledRun().closed == []
    assert set(_closed().entry()) == {"app", "title", "href", "by", "at"}


def test_closeout_claims_put_done_first_with_continuous_positions() -> None:
    cards = [{"signal_id": "email:acc:t1", "app": "email", "kind": "needs_reply",
              "group": "needs_you", "score": 50, "title": "T", "state": "new",
              "facts": {"who": "Priya"}, "href": "/email?x=1", "fingerprint": None, "at": None}]
    got = gather.closeout_claims([_closed(), _closed("approvals:9", "Nine", "/approvals")],
                                 cards, NOW)
    assert [(c.position, c.section, c.source_id) for c in got] == [
        (0, "done", "tasks:42"), (1, "done", "approvals:9"), (2, "moved", "email:acc:t1")]
    assert gather.closeout_lead(D, got) == "Your close-out, Tue 10 Mar. 2 done and 1 moved to tomorrow."


def test_a_row_with_no_in_app_href_or_no_known_prefix_makes_no_claim() -> None:
    rows = [_closed(href="https://evil.test/x"), _closed("nobody:1"), _closed(title=" ")]
    assert gather.closeout_claims(rows, [], NOW) == []
    assert gather.closeout_lead(D, []) is None


def test_the_brief_claim_still_refuses_a_section_outside_the_brief_groups() -> None:
    card = {"signal_id": "email:acc:t1", "kind": "needs_reply", "group": "fyi",
            "href": "/email", "facts": {}, "title": "T"}
    assert gather.claim_of(card, 0, NOW) is None
    assert gather.claim_of(card, 0, NOW, section="moved") is not None


def test_the_done_rows_are_cut_to_the_brief_lines() -> None:
    rows = [_closed(f"tasks:{i}", f"T{i}") for i in range(12)]
    assert len(gather.closeout_claims(rows, [], NOW)) == 7
