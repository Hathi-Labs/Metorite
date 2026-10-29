"""WS-BRIEF BR-2a-1 — the planner, the gather and the flags, with no database.

Spec: ``project-docs/specs/core_brief.md`` §4.1 to §4.3, §9 and §10 row 2.
Code: ``gateway/brief/{flags,planner,gather,run}.py``, the seed hooks of
``gateway/jobs/worker.py``, and ``acb_common.assistant_profile.app_prefs``.
The database half is ``tests/unit/test_brief_run_r8.py``.

**The prefix and href of each kind.** ``test_every_feed_kind_has_a_known_
prefix_and_an_href`` reads the ``Signal(`` calls of ``routes/shell/feed.py``
and ``needs.py``, and lists the ``signal_id`` prefix and the href of each
kind. A kind whose prefix is not in ``gather.SOURCE_APPS``, or with no href,
fails here. A gap is a finding for the board, and the gather invents no
link.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import assistant_profile
from gateway.brief import flags, gather, planner, store
from gateway.brief import run as brief_run
from gateway.jobs import worker
from gateway.reach import plan
from gateway.routes.shell import state

_ROOT = Path(__file__).resolve().parents[2]
_SHELL = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "shell"

KOLKATA = ZoneInfo("Asia/Kolkata")
#: Tuesday 10 March 2099, 07:10 in Asia/Kolkata (01:40 UTC).
NOW = datetime(2099, 3, 10, 1, 40, tzinfo=UTC)
D = date(2099, 3, 10)


def card(sid: str, kind: str = "needs_reply", **kw) -> dict:
    facts = kw.pop("facts", None) or (
        {"who": "Priya", "waiting_since": (NOW - timedelta(minutes=5)).isoformat()}
        if kind == "needs_reply" else {})
    base = {"signal_id": sid, "app": sid.split(":")[0], "kind": kind, "group": "needs_you",
            "score": 50, "title": "T", "facts": facts, "fingerprint": "fp000001",
            "state": "new", "at": None, "why": "W", "href": f"/{sid.split(':')[0]}?x=1"}
    return {**base, **kw}


# ── The flags ───────────────────────────────────────────────────────────────


def _settings(monkeypatch, **kw) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**kw))


def test_the_brief_is_dark_by_default(monkeypatch) -> None:
    from acb_common.env_guard import PLATFORM_ENV_PREFIXES, is_platform_env
    from acb_common.settings import Settings

    assert Settings.model_fields["brief_enabled"].default is False
    assert Settings.model_fields["brief_orgs"].default == ""
    assert "BRIEF_" in PLATFORM_ENV_PREFIXES
    assert is_platform_env("BRIEF_ENABLED") and is_platform_env("BRIEF_ORGS")
    _settings(monkeypatch, brief_enabled=False, brief_orgs="org-a")
    assert flags.enabled() is False and flags.org_allowed("org-a") is False


def test_an_org_needs_the_switch_and_the_list(monkeypatch) -> None:
    _settings(monkeypatch, brief_enabled=True, brief_orgs=" Org-A , org-b")
    assert flags.orgs() == {"org-a", "org-b"}
    assert flags.org_allowed("ORG-A") and flags.org_allowed("org-b")
    assert not flags.org_allowed("org-c") and not flags.org_allowed(None)
    _settings(monkeypatch, brief_enabled=True, brief_orgs="*")
    assert flags.orgs() == frozenset() and not flags.org_allowed("org-a")
    _settings(monkeypatch, brief_enabled=True, brief_orgs="")
    assert not flags.org_allowed("org-a")


# ── The preference seam ─────────────────────────────────────────────────────


def test_the_brief_declares_its_three_preferences_in_the_profile_seam() -> None:
    declared = assistant_profile.APP_PREFERENCES["brief"]
    assert declared == {"enabled": True, "brief_time": "07:30", "closeout_time": "18:30"}
    assert assistant_profile.app_prefs(None, "brief") == declared
    assert assistant_profile.app_prefs({"v": 1}, "brief") == declared
    stored = '{"apps": {"brief": {"enabled": false, "brief_time": "09:00", '
    stored += '"closeout_time": "25:00", "other": 1}}}'
    got = assistant_profile.app_prefs(stored, "brief")
    assert got == {"enabled": False, "brief_time": "09:00", "closeout_time": "18:30"}
    assert assistant_profile.app_prefs({"apps": {"brief": {"enabled": "no"}}}, "brief")[
        "enabled"] is True
    with pytest.raises(KeyError):
        assistant_profile.app_prefs(None, "nobody")


@pytest.mark.parametrize("value, want", [
    ("07:30", "07:30"), ("23:59", "23:59"), ("00:00", "00:00"), ("7:30", None),
    ("24:00", None), ("07:60", None), ("07:30:00", None), (730, None), (None, None)])
def test_a_clock_time_is_hh_mm(value, want) -> None:
    assert assistant_profile.clock_time(value) == want


def test_a_member_takes_the_stored_times_and_the_defaults() -> None:
    prefs = assistant_profile.app_prefs
    m = planner.member_of(" Ana@Org.test ", prefs(None, "brief"))
    assert m == planner.Member("ana@org.test", True, time(7, 30), time(18, 30))
    m = planner.member_of("ana@org.test", prefs({"apps": {"brief": {"brief_time": "09:00"}}},
                                                "brief"))
    assert m.brief_time == time(9, 0) and m.closeout_time == time(18, 30)
    off = prefs({"apps": {"brief": {"enabled": False}}}, "brief")
    assert planner.member_of("x@y", off).enabled is False


# ── The slot ────────────────────────────────────────────────────────────────


def test_the_slot_is_the_brief_time_on_the_calendar_date_in_the_zone() -> None:
    local_date, when = planner.slot(KOLKATA, time(7, 30), NOW)
    assert local_date == D
    assert when == datetime(2099, 3, 10, 7, 30, tzinfo=KOLKATA)
    assert when.astimezone(UTC) == datetime(2099, 3, 10, 2, 0, tzinfo=UTC)
    assert planner.slot(KOLKATA, time(9, 0), NOW) is None  # outside the 30 minutes
    at_nine = datetime(2099, 3, 10, 8, 45, tzinfo=KOLKATA)
    assert planner.slot(KOLKATA, time(9, 0), at_nine) == (
        D, datetime(2099, 3, 10, 9, 0, tzinfo=KOLKATA))


def test_the_slot_looks_back_two_hours_and_crosses_midnight() -> None:
    """Repair round 1, P1-2: a slot that no tick planned, up to the
    look-back ago, is still a slot."""
    seven_thirty = datetime(2099, 3, 10, 7, 30, tzinfo=KOLKATA)
    assert planner.slot(KOLKATA, time(7, 30), seven_thirty) == (D, seven_thirty)
    a_minute_late = seven_thirty + timedelta(minutes=1)
    assert planner.slot(KOLKATA, time(7, 30), a_minute_late) == (D, seven_thirty)
    edge = seven_thirty + planner.LOOKBACK
    assert planner.slot(KOLKATA, time(7, 30), edge) == (D, seven_thirty)
    assert planner.slot(KOLKATA, time(7, 30), edge + timedelta(seconds=1)) is None
    # 00:10 of the 11th, seen at 23:50 of the 10th: the date is the 11th.
    late = datetime(2099, 3, 10, 23, 50, tzinfo=KOLKATA)
    assert planner.slot(KOLKATA, time(0, 10), late) == (
        date(2099, 3, 11), datetime(2099, 3, 11, 0, 10, tzinfo=KOLKATA))
    # 23:30 of the 10th, seen at 00:30 of the 11th: the date is the 10th.
    after = datetime(2099, 3, 11, 0, 30, tzinfo=KOLKATA)
    assert planner.slot(KOLKATA, time(23, 30), after) == (
        D, datetime(2099, 3, 10, 23, 30, tzinfo=KOLKATA))


def test_a_slot_past_the_look_back_is_missed_inside_the_scan() -> None:
    seven_thirty = datetime(2099, 3, 10, 7, 30, tzinfo=KOLKATA)
    past = seven_thirty + planner.LOOKBACK + timedelta(seconds=1)
    assert planner.missed(KOLKATA, time(7, 30), past) == (D, seven_thirty)
    assert planner.missed(KOLKATA, time(7, 30), seven_thirty + planner.LOOKBACK) is None
    far = seven_thirty + planner.LOOKBACK + planner.MISSED_SCAN + timedelta(seconds=1)
    assert planner.missed(KOLKATA, time(7, 30), far) is None
    assert planner.missed(KOLKATA, time(7, 30), NOW) is None  # still ahead
    assert timedelta(hours=2) == planner.LOOKBACK


def test_a_member_with_no_zone_takes_the_org_zone() -> None:
    """Repair round 1, P2-h. UTC is what a member with no zone reads."""
    utc = ZoneInfo("UTC")
    assert planner.member_zone(utc, KOLKATA) == KOLKATA
    assert planner.member_zone(state.zone_of(None), KOLKATA) == KOLKATA
    assert planner.member_zone(ZoneInfo("Europe/Berlin"), KOLKATA) == ZoneInfo("Europe/Berlin")
    assert planner.member_zone(utc, utc) == utc


def test_the_slot_date_is_the_calendar_date_and_not_the_feed_day() -> None:
    """Mutation M5: a day that starts at 09:00 has a feed day of the 9th at
    07:30 on the 10th. The brief of the 10th still names the 10th."""
    day = state.Day(KOLKATA, 9, 18)
    assert day.feed_day(NOW) == date(2099, 3, 9)
    assert planner.slot(day.tz, time(7, 30), NOW)[0] == D


def test_the_dedupe_key_and_the_lead() -> None:
    assert planner.dedupe_key("ana@org.test", "brief", D) == "brief:ana@org.test:brief:2099-03-10"
    assert timedelta(minutes=5) == planner.LEAD
    assert timedelta(minutes=30) == planner.HORIZON
    assert planner.HORIZON >= 2 * worker.SEED_BUCKET  # two ticks see each slot
    assert planner.LOOKBACK > planner.HORIZON


# ── The gather ──────────────────────────────────────────────────────────────


def test_source_app_is_the_prefix_of_the_signal_id_and_not_the_app() -> None:
    c = card("tasks:42", "block_soon", app="calendar", group="now")
    claim = gather.claim_of(c, 0, NOW)
    assert claim is not None
    assert claim.source_app == "tasks" and claim.source_id == "tasks:42"
    assert claim.source_kind == "block_soon" and claim.section == "now"
    assert claim.text == plan.line(c, NOW) and claim.href == "/tasks?x=1"
    assert claim.fingerprint == "fp000001" and claim.position == 0


def test_a_card_with_no_known_prefix_or_no_href_gets_no_claim(caplog) -> None:
    assert gather.source_app("nobody:1") is None
    assert gather.source_app("tasks") is None and gather.source_app("tasks:") is None
    assert gather.source_app(None) is None
    assert gather.claim_of(card("nobody:1"), 0, NOW) is None
    assert gather.claim_of(card("email:a:t1", href=None), 0, NOW) is None
    assert gather.claim_of(card("email:a:t1", href="https://x.test/"), 0, NOW) is None
    assert gather.claim_of(card("email:a:t1", href="//x.test/"), 0, NOW) is None
    # The one in-app check (repair round 1, P2-g): a dot segment, a
    # backslash, an API path and an encoded slash are refused too.
    for bad in ("/projects/../crm", "/\\x.test", "/api/x", "/email/%2F%2Fx.test",
                "/email\tx"):
        assert gather.in_app_href(bad) is None, bad
    assert gather.in_app_href("/email?account=a&thread=t1") == "/email?account=a&thread=t1"
    assert gather.claim_of(card("email:a:t1", group="fyi"), 0, NOW) is None
    no_fp = gather.claim_of(card("email:a:t1", fingerprint=None), 0, NOW)
    assert no_fp is not None and no_fp.fingerprint is None


def test_claims_follow_the_shown_cards_and_skip_the_bad_ones() -> None:
    cards = [card("email:a:t1"), card("nobody:2", "approval"), card("tasks:3", "overdue"),
             card("tasks:4", "due_today", group="fyi")]
    got = gather.claims(cards, NOW)
    shown = gather.shown(cards)
    # The feed's order, and the fyi card is not shown.
    assert {c["signal_id"] for c in shown} == {"tasks:3", "nobody:2", "email:a:t1"}
    assert [c.source_id for c in got] == [
        c["signal_id"] for c in shown if c["signal_id"] != "nobody:2"]
    # The position is the index among the shown cards, so the skip leaves a hole.
    assert [c.position for c in got] == [
        i for i, c in enumerate(shown) if c["signal_id"] != "nobody:2"]
    assert all(c.text == plan.line(shown[c.position], NOW) for c in got)
    many = [card(f"email:a:t{i}") for i in range(12)]
    assert len(gather.claims(many, NOW)) == plan.BRIEF_LINES


_SIGNAL = re.compile(r"Signal\((.*?)\n\s*\)", re.DOTALL)
_SID = re.compile(r"(?:signal_id|id)=(?:f)?\"([a-z_]+):")
_KIND = re.compile(r"kind=\"([a-z_]+)\"|kind=kind")
_HREF = re.compile(r"\bhref=")


def test_every_feed_kind_has_a_known_prefix_and_an_href() -> None:
    """The list of the prefix and the href of each ``Signal(`` call. A new
    provider with a prefix outside ``SOURCE_APPS`` fails here, and the gap
    goes to the board before the gather drops its cards in silence."""
    rows: list[tuple[str, str, str, bool]] = []
    for name in ("feed.py", "needs.py"):
        source = (_SHELL / name).read_text(encoding="utf-8")
        for block in _SIGNAL.finditer(source):
            body = block.group(1)
            sid = _SID.search(body)
            kind = _KIND.search(body)
            if sid is None and "signal_id=" not in body:
                continue
            prefix = sid.group(1) if sid else "?"
            kind_name = (kind.group(1) or "<by call>") if kind else "?"
            rows.append((name, kind_name, prefix, bool(_HREF.search(body))))
    assert rows, "no Signal( call was found"
    # The two day cards share `day_key`, and the needs-reply row keeps its id.
    # Each is built from a known prefix above its call.
    known = {"calendar", "email", "approvals"}
    gaps = [r for r in rows if (r[2] != "?" and r[2] not in gather.SOURCE_APPS) or not r[3]]
    unread = [r for r in rows if r[2] == "?"]
    assert not gaps, f"a kind with an unknown prefix or no href: {gaps}"
    assert all(r[1] in ("day_overbooked", "no_plan_yet", "needs_reply", "approval", "?")
               for r in unread), unread
    assert known <= gather.SOURCE_APPS
    assert "approvals" in gather.SOURCE_APPS and "reminders" in gather.SOURCE_APPS


# ── The wiring ──────────────────────────────────────────────────────────────


def test_the_two_kinds_are_detached_and_idempotent_and_the_hooks_are_in() -> None:
    assert planner.PLAN_KIND in worker.kinds() and planner.RUN_KIND in worker.kinds()
    for kind in (planner.PLAN_KIND, planner.RUN_KIND):
        entry = worker._HANDLERS[kind]
        assert entry.mode == "detached" and entry.idempotent is True
    assert planner.seed in worker.seed_hooks
    assert brief_run.prune in worker.prune_hooks
    assert worker.seed_hooks.count(planner.seed) == 1
    brief_run.register_jobs(replace=True)
    assert worker.seed_hooks.count(planner.seed) == 1
    assert worker.prune_hooks.count(brief_run.prune) == 1
    assert brief_run.RETENTION_DAYS == 90
    assert brief_run.LEDGER_JOB == "brief.brief"


def test_main_imports_the_brief_before_the_worker_starts_in_its_own_try() -> None:
    """Repair round 1, P2-i: an import error of the brief cannot stop the
    queue, so the import has its own ``try``, closed before the worker's."""
    import ast

    main = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    assert main.index("import gateway.brief.run") < main.index("await start_job_worker()")
    tries = [n for n in ast.walk(ast.parse(main)) if isinstance(n, ast.Try)]

    def holds(node: ast.Try, needle: str) -> bool:
        return any(needle in ast.unparse(stmt) for stmt in node.body)

    brief = [t for t in tries if holds(t, "gateway.brief.run")]
    assert len(brief) == 1 and not holds(brief[0], "start_job_worker")
    assert brief[0].handlers and any(holds(t, "start_job_worker") for t in tries)


def test_the_store_moves_are_the_four_of_the_run_and_the_delivery() -> None:
    # BR-2c-1 adds ready to delivered: the first read of GET /brief/today.
    assert {("planned", "gathered"), ("gathered", "ready"), ("planned", "empty"),
            ("planned", "failed"), ("ready", "delivered")} == store.TRANSITIONS
    assert store.LEAD_SOURCES == ("template", "model")


def test_the_seed_bucket_is_fifteen_minutes_in_utc() -> None:
    t = datetime(2099, 3, 10, 7, 44, 59, 999, tzinfo=KOLKATA)
    assert worker.seed_bucket(t) == "2099-03-10T02:00"  # 07:44 IST = 02:14 UTC
    assert worker.seed_bucket(datetime(2099, 3, 10, 2, 15, tzinfo=UTC)) == "2099-03-10T02:15"
    assert worker.seed_bucket(datetime(2099, 3, 10, 2, 29, 59, tzinfo=UTC)) == "2099-03-10T02:15"


def test_the_run_payload_is_strict() -> None:
    with pytest.raises(ValueError):
        brief_run._payload({"member": "", "local_date": "2099-03-10",
                            "slot_at": "2099-03-10T07:30:00+05:30"})
    with pytest.raises(ValueError):
        brief_run._payload({"member": "a@b", "local_date": "2099-03-10",
                            "slot_at": "2099-03-10T07:30:00"})
    member, local_date, slot_at = brief_run._payload({
        "member": " Ana@B ", "local_date": "2099-03-10", "slot_at": "2099-03-10T07:30:00+05:30"})
    assert member == "ana@b" and local_date == D and slot_at.utcoffset() == timedelta(hours=5, minutes=30)


def test_the_gather_imports_no_model_and_no_send() -> None:
    """The slice calls no model and sends nothing (``core_brief.md`` §10)."""
    for name in ("gather.py", "planner.py", "run.py"):
        src = (_ROOT / "apps/services/gateway/gateway/brief" / name).read_text(encoding="utf-8")
        assert "acompletion" not in src and "outbox" not in src and "whatsapp" not in src
