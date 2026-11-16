"""WS-PZ MM-1 — the act record: the one writer, no text, the gates (hermetic).

Spec: ``personalization.md`` §19.4 and §19.10. Code:
``packages/acb_common/acb_common/member_actions.py``. The R8 twin on a real
database is ``tests/unit/test_member_action_r8.py``.

a. One writer: no other source runs an INSERT on ``member_action``.
b. No text: a ``subject`` or ``body`` key, an unknown key, and a text value
   above 120 characters are refused. The INSERT names no text column.
c. A closed set of acts. A rule act is written and never counts as a choice.
d. The gates: the flag off, no org, or ``learning`` off writes nothing, and a
   failure of the write answers False and never raises.
e. Each seam of MM-1 calls the writer, with the right act.

Mutation (R7): add a second ``INSERT INTO member_action`` to any source, and
(a) goes red. Add ``subject`` to ``FEATURE_KEYS``, and (b) goes red.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import member_actions as ma

_ROOT = Path(__file__).resolve().parents[2]
_WRITER = _ROOT / "packages" / "acb_common" / "acb_common" / "member_actions.py"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
ORG = "11111111-1111-1111-1111-111111111111"
ME = "Ana@Example.com"


def _kw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(member=ME, app="email", act="forward", via="app",
                                item_kind="email", item_id="m1")
    base.update(over)
    return base


# ── a. One writer ──────────────────────────────────────────────────────────

def test_only_the_writer_inserts_into_the_table() -> None:
    insert = re.compile(r"INSERT\s+INTO\s+member_action\b", re.I)
    found = []
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if path == _WRITER or "node_modules" in path.parts:
                continue
            if insert.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(str(path.relative_to(_ROOT)))
    assert found == []
    assert len(insert.findall(_WRITER.read_text(encoding="utf-8"))) == 1


# ── b. No text ─────────────────────────────────────────────────────────────

def test_the_insert_names_no_text_column() -> None:
    cols = ma.INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    assert "organization_id" not in cols
    for banned in ("subject", "body", "snippet", "text"):
        assert banned not in cols


@pytest.mark.parametrize("key", ["subject", "body", "snippet", "text", "unknown"])
def test_a_feature_key_outside_the_list_is_refused(key: str) -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={key: "x"}))


def test_a_long_text_value_is_refused_and_a_short_one_kept() -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"sender_domain": "a" * 121}))
    row = ma.build_row(**_kw(features={"sender_domain": "ge.com", "edit_distance": 4,
                                       "is_reply": False, "outcome": None}))
    assert '"sender_domain": "ge.com"' in row["features"]
    assert "outcome" not in row["features"]


def test_no_feature_key_can_hold_a_subject() -> None:
    assert not {"subject", "body", "text", "snippet", "preview"} & ma.FEATURE_KEYS


def test_a_value_that_is_not_a_scalar_is_refused() -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"sender_domain": ["a"]}))
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"edit_distance": float("nan")}))


# ── c. The closed set ──────────────────────────────────────────────────────

def test_the_acts_are_the_closed_set_of_the_spec() -> None:
    assert ma.ACTS["email"] >= {"reply", "forward", "send", "mark_done", "archive",
                                "snooze", "label", "delegate", "draft_sent"}
    assert ma.ACTS["chat"] == {"card_approve", "card_reject", "card_revise"}
    assert {"card_acted", "kind_mute"} <= ma.ACTS["my_day"]
    assert ma.ACTS["reach"] == {"alert_useful", "alert_not_useful"}


@pytest.mark.parametrize("over", [
    {"app": "nope"}, {"act": "nope"}, {"app": "chat", "act": "forward"},
    {"via": "robot"}, {"item_id": ""}, {"item_kind": "x" * 41},
    {"member": "anonymous"}, {"member": ""},
])
def test_a_bad_act_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(**over))


def test_the_member_is_lowered_and_the_targets_are_clean() -> None:
    row = ma.build_row(**_kw(targets=["Veena@GE.com", "veena@ge.com", "Kiran@ge.com"]))
    assert row["m"] == "ana@example.com"
    assert row["targets"] == ["veena@ge.com", "kiran@ge.com"]
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(targets=["not-an-address"]))
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(targets=[f"a{i}@x.com" for i in range(21)]))


def test_only_app_and_card_count_as_a_choice() -> None:
    assert ma.counts_as_choice("app") and ma.counts_as_choice("card")
    assert not ma.counts_as_choice("rule")
    assert ma.build_row(**_kw(via="rule"))["via"] == "rule"


def test_domain_of() -> None:
    assert ma.domain_of("Bob <bob@GE.com>") == "ge.com"
    assert ma.domain_of("nobody") is None and ma.domain_of(None) is None


# ── d. The gates ───────────────────────────────────────────────────────────

class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _Db:
    def __init__(self, learning: Any = True, boom: bool = False) -> None:
        self.sql: list[str] = []
        self.learning = learning
        self.boom = boom

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        self.sql.append(sql)
        if sql.startswith("INSERT") and self.boom:
            raise RuntimeError("secret value in message")
        if sql.startswith("SELECT"):
            return SimpleNamespace(first=lambda: None if self.learning is None else
                                   ({"v": 1, "learning": self.learning},))
        return SimpleNamespace(rowcount=1)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "member_actions_orgs", value, raising=False)


def test_the_flag_is_off_by_default_and_reads_star(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    assert get_settings().member_actions_orgs == ""
    assert not ma.enabled_for(ORG)
    _flag(monkeypatch, ORG.upper())
    assert ma.enabled_for(ORG) and not ma.enabled_for("other") and not ma.enabled_for(None)
    _flag(monkeypatch, "*")
    assert ma.enabled_for(ORG) and not ma.enabled_for("")


def test_the_flag_off_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db()
    assert _run(ma.record_act(db, org=ORG, **_kw())) is False
    assert db.sql == []


def test_a_row_is_written_when_the_flag_holds_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    db = _Db()
    assert _run(ma.record_act(db, org=ORG, **_kw())) is True
    assert [s.split()[0] for s in db.sql] == ["SELECT", "INSERT"]
    assert _run(ma.record_act(db, org="", **_kw())) is False


def test_learning_off_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    db = _Db(learning=False)
    assert _run(ma.record_act(db, org=ORG, **_kw())) is False
    assert not any(s.startswith("INSERT") for s in db.sql)


def test_no_profile_row_counts_as_learning_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    assert _run(ma.record_act(_Db(learning=None), org=ORG, **_kw())) is True


def test_a_failed_write_answers_false_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    assert _run(ma.record_act(_Db(boom=True), org=ORG, **_kw())) is False
    assert _run(ma.record_act(_Db(), org=ORG, **_kw(act="nope"))) is False


def test_the_writer_never_names_the_profile_column() -> None:
    src = _WRITER.read_text(encoding="utf-8")
    assert "SELECT assistant_profile" not in src
    assert "FROM user_settings" not in src


# ── e. The seams ───────────────────────────────────────────────────────────

_SEAMS = {
    "routes/email/automation/replyzero.py": ['act="mark_done"'],
    "routes/email/transport/send.py": ['act="reply" if req.reply_to_message_id else "send"'],
    "routes/email/transport/forward.py": ['act="forward"'],
    "routes/email/transport/messages.py": ['act="archive"', 'act="label"', 'act="snooze"'],
}


@pytest.mark.parametrize("rel", sorted(_SEAMS))
def test_each_seam_calls_the_one_writer(rel: str) -> None:
    src = (_GATEWAY / rel).read_text(encoding="utf-8")
    assert "member_actions" in src
    for needle in _SEAMS[rel]:
        assert needle in src, (rel, needle)
    # A seam passes the org of the session, never a request value.
    assert "org=user.organization_id" in src
    assert "req.organization" not in src


def test_the_prune_hook_is_registered_and_the_purge_names_the_table() -> None:
    from gateway import member_action_prune
    from gateway.jobs import worker
    from gateway.routes.admin.members import _PURGE_DELETES

    assert member_action_prune.prune_hook in worker.prune_hooks
    [rows] = [r for r in _PURGE_DELETES if r.table == "member_action"]
    assert rows.where.startswith("lower(member_email) = :email AND organization_id = ")
    assert "180" not in ma.PRUNE_SQL and ma.RETENTION_DAYS == 180
