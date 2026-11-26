"""Mark done in Email: the flag, and the statement with the flag off (task 3995).

Spec: ``email_app_master_plan.md`` §6.6. The behaviour on a real database is in
``test_email_mark_done_r8.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.email.automation import mark_done, replyzero

ROOT = Path(__file__).resolve().parents[2]
ORG = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER = "1f8fad5b-d9cb-469f-a165-70867728950e"


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_mark_done_orgs", value)


# ── 1. The flag ──────────────────────────────────────────────────────────────

def test_the_flag_is_empty_by_default_and_reads_off(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["email_mark_done_orgs"].default == ""
    _flag(monkeypatch, "")
    assert not mark_done.mark_done_on(ORG)
    assert not mark_done.mark_done_on()


def test_the_flag_takes_the_org_of_the_binding(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    _flag(monkeypatch, f" {ORG.upper()} ,")
    assert mark_done.mark_done_on(ORG)
    assert not mark_done.mark_done_on(OTHER)
    # No tenant under a list of ids reads off (fail closed).
    assert not mark_done.mark_done_on()
    for org, expected in ((ORG, True), (OTHER, False)):
        token = bind_tenant(org)
        try:
            assert mark_done.mark_done_on() is expected
        finally:
            release_tenant(token)
    _flag(monkeypatch, "*")
    assert mark_done.mark_done_on(OTHER) and mark_done.mark_done_on()


def test_the_reader_fails_closed(monkeypatch) -> None:
    import acb_common

    def _boom() -> Any:
        raise RuntimeError("settings fault")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert mark_done.mark_done_on(ORG) is False


def test_the_flag_reloads_and_stays_operator_only() -> None:
    from acb_common.env_guard import is_platform_env
    from gateway import flag_reload as fr

    assert fr.reloadable("EMAIL_MARK_DONE_ORGS")
    assert is_platform_env("EMAIL_MARK_DONE_ORGS")
    doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "`EMAIL_MARK_DONE_ORGS`" in doc and "mark_done_on" in doc
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nEMAIL_MARK_DONE_ORGS=\n" in example


def test_no_module_reads_the_setting_but_its_one_reader() -> None:
    hits = []
    for base in (ROOT / "packages", ROOT / "apps"):
        for p in base.rglob("*.py"):
            if ".venv" in p.parts:
                continue
            if "email_mark_done_orgs" in p.read_text(encoding="utf-8", errors="replace"):
                hits.append(p.relative_to(ROOT).as_posix())
    assert sorted(hits) == [
        "apps/services/gateway/gateway/routes/email/automation/mark_done.py",
        "packages/acb_common/acb_common/settings.py",
    ]


# ── 2. The statement ─────────────────────────────────────────────────────────

class _Db:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(str(statement))
        self.params.append(params)

        class _Result:
            def fetchone(self) -> tuple[int]:
                return (1,)

            def fetchall(self) -> list:
                return []

        return _Result()


async def _upsert(**kwargs: Any) -> str:
    db = _Db()
    await replyzero._upsert_thread_status(
        db, "a", "t", "FYI", "m", None, "r", **kwargs)
    assert len(db.statements) == 1
    return db.statements[0]


@pytest.mark.parametrize("kwargs", [
    {}, {"preserve_done": True}, {"guard": True},
    {"preserve_done": True, "guard": True}])
async def test_with_the_flag_off_the_upsert_is_the_statement_of_before(
    kwargs, monkeypatch,
) -> None:
    _flag(monkeypatch, "")
    sql = await _upsert(**kwargs)
    assert "held_m" not in sql
    assert ("             last_message_id = EXCLUDED.last_message_id,\n"
            "             last_message_at = EXCLUDED.last_message_at,\n"
            "             reason = EXCLUDED.reason, classified_at = now(),\n") in sql


async def test_with_the_flag_on_only_an_automated_upsert_holds(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    # A write of the member: the statement of before.
    assert "held_m" not in await _upsert()
    for kwargs in ({"preserve_done": True}, {"guard": True}):
        sql = await _upsert(**kwargs)
        assert sql.count("held_m.received_at >") == 4, kwargs
        # The message id still moves, so the backfill does not ask again.
        assert "last_message_id = EXCLUDED.last_message_id," in sql


async def test_an_automated_write_cannot_forge_the_mark(monkeypatch) -> None:
    """The reason of a rule match is model text over the mail."""
    _flag(monkeypatch, "*")
    for kwargs, stored in (({"preserve_done": True}, "Auto-classified"),
                           ({"guard": True}, "Auto-classified"),
                           ({}, mark_done.MANUAL_DONE_REASON)):
        db = _Db()
        await replyzero._upsert_thread_status(
            db, "a", "t", "DONE", "m", None, mark_done.MANUAL_DONE_REASON, **kwargs)
        assert db.params[-1]["reason"] == stored, kwargs
    # With the flag off, the write is the write of before.
    _flag(monkeypatch, "")
    db = _Db()
    await replyzero._upsert_thread_status(
        db, "a", "t", "DONE", "m", None, mark_done.MANUAL_DONE_REASON, guard=True)
    assert db.params[-1]["reason"] == mark_done.MANUAL_DONE_REASON


async def test_with_the_flag_off_the_hold_reads_nothing(monkeypatch) -> None:
    _flag(monkeypatch, "")
    db = _Db()
    assert not await mark_done.thread_is_held(db, "a", "t")
    assert await mark_done.held_label(db, "a", "t", "Needs Reply") == "Needs Reply"
    assert await mark_done.held_among("a", ["t"]) == set()
    await mark_done.reclean_returned_labels("a", [(object(), [])])
    assert db.statements == []
