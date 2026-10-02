"""Automatic reply drafting is OFF for a new mailbox (D-EM-6).

    "Turn the default autodraft emails to off."             — owner, 2026-10-02

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.2 D-EM-6 and §10.4
EM-T7. A member turns drafting on in AI settings. Every draft is a call on the
drafting model, so nobody should find it already running.

History. Migration 82 (2026-07-20) set live drafting ON, because a draft on mail
that just arrived was then the feature. D-EM-6 reverses that for a NEW mailbox.
Migration 224 changes no existing row.

Every path that can turn drafting on for a new mailbox is pinned here:

  * ``AssistantSettingsModel.draft_replies`` — a PUT that omits the field.
  * The GET fallback — a mailbox with no settings row. The agent's
    ``update_assistant_settings`` PUTs that body back, so an ON fallback turned
    drafting on whenever the agent saved another field.
  * The column default — an INSERT that omits the column.
    ``derive_writing_style`` inserts only ``(account_id, writing_style)``.
  * The Needs Reply preset — ``test_email_presets.py`` pins it.

``follow_up_auto_draft`` stays OFF (migration 81) and is pinned beside it. The
backfill is pinned by ``test_email_process_past_drafting.py``.

R8: ``TestMigration224OnARealDatabase`` builds a PRIVATE database on the server
that ``TENANT_LADDER_DATABASE_URL`` names, applies the ladder up to 224, seeds
rows, applies the rest and drops the database. It never touches the shared
ladder database.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_auto_draft_defaults.py -v -rs
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import rules as rules_mod
from gateway.routes.email.automation.assistant import AssistantSettingsModel

from tests.unit._email_fakes import bind_db

REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = REPO / "infra/postgres"
_ASSISTANT = (
    REPO / "apps/services/gateway/gateway/routes/email/automation/assistant.py"
).read_text(encoding="utf-8")
_FIXTURE = json.loads(
    (REPO / "tests/fixtures/email_new_mailbox_settings.json")
    .read_text(encoding="utf-8")
)["get_without_settings_row"]
_AGENT = REPO / "apps/agents/agent-email-assistant/agents.py"


def _migration(name: str) -> str:
    return (_MIGRATIONS / name).read_text(encoding="utf-8")


def _migration_224() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    hits = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if "D-EM-6" in p.read_text(encoding="utf-8")
        and "ALTER COLUMN draft_replies SET DEFAULT false"
        in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one D-EM-6 default migration, got {hits}"
    return hits[0]


def _column_default(col: str) -> str:
    """The default `col` ends up with after every migration is applied, in order.
    Read from the files rather than asserted per-file, so a later migration
    flipping it back cannot leave an earlier assertion passing and wrong."""
    value = "true"  # as created (migrations 26 and 29)
    for path in sorted(_MIGRATIONS.glob("*.sql"),
                       key=lambda p: int(p.name.split("_")[0])
                       if p.name.split("_")[0].isdigit() else 0):
        for m in re.finditer(
            rf"ALTER COLUMN {col} SET DEFAULT (true|false)",
            path.read_text(encoding="utf-8"),
        ):
            value = m.group(1)
    return value


# ── live drafting: OFF for a new mailbox (D-EM-6) ───────────────────────────


def test_live_reply_drafting_is_off_by_default() -> None:
    """The model default is what a PUT without the field stores."""
    assert AssistantSettingsModel(account_id="acc-1").draft_replies is False


def test_the_live_column_default_ends_up_off() -> None:
    assert _column_default("draft_replies") == "false"


def _put_db() -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(
            return_value=SimpleNamespace(email_address="me@acme.test")))
    return db


async def test_a_save_without_the_field_stores_false_and_adds_no_draft_action() -> None:
    """A body with no ``draft_replies`` goes through the REAL PUT handler. The
    INSERT binds false, and the Needs Reply sync is told to REMOVE drafting."""
    db = _put_db()
    sync = AsyncMock(return_value=False)
    req = AssistantSettingsModel.model_validate({"account_id": "acc-1"})
    user = SimpleNamespace(email="me@acme.test")
    with patch.object(assistant_mod, "_tenant_session", bind_db(db)), \
            patch.object(assistant_mod, "_assert_account_owner", AsyncMock()), \
            patch.object(rules_mod, "sync_draft_reply_action", sync):
        res = await assistant_mod.put_assistant_settings(req=req, user=user)
    insert = next(
        c.args[1] for c in db.execute.call_args_list
        if len(c.args) > 1 and "INSERT INTO email_assistant_settings" in str(c.args[0])
    )
    assert insert["dr"] is False
    sync.assert_awaited_once_with(db, "acc-1", False)
    assert res["draft_replies"] is False


# ── the GET for a mailbox with no settings row ──────────────────────────────
# This is the default a member actually SEES. The AI settings toggle draws from
# it (the vitest half reads the same fixture), and the agent PUTs it back.


async def _get_without_row() -> dict:
    """Run the REAL GET handler for a mailbox with no settings row."""
    settings_res = MagicMock(fetchone=MagicMock(return_value=None))
    account_res = MagicMock(
        fetchone=MagicMock(
            return_value=SimpleNamespace(email_address="me@acme.test")))
    db = AsyncMock()
    db.execute.side_effect = [settings_res, account_res]
    user = SimpleNamespace(email="me@acme.test")
    with patch.object(assistant_mod, "_tenant_session", bind_db(db)), \
            patch.object(assistant_mod, "_assert_account_owner", AsyncMock()):
        return await assistant_mod.get_assistant_settings(
            account_id="acc-1", user=user)


async def test_the_get_for_a_new_mailbox_answers_the_shared_fixture() -> None:
    body = await _get_without_row()
    assert {k: body[k] for k in _FIXTURE} == _FIXTURE
    # The line above holds the GET to the fixture, and the vitest half holds the
    # toggle to it. A fixture flipped to true would leave both halves agreeing
    # on ON, so these two lines pin the decision itself (D-EM-6).
    assert _FIXTURE["draft_replies"] is False
    assert _FIXTURE["follow_up_auto_draft"] is False


def _get_fallback(field: str) -> str:
    """The literal the /assistant/settings GET falls back to for `field`."""
    m = re.search(
        rf'"{field}": \(\s*(?:bool\(.*?\)\s*)?if .*?else (True|False)\s*\)',
        _ASSISTANT, re.DOTALL,
    )
    assert m, f"could not find the GET fallback for {field}"
    return m.group(1)


def test_the_get_fallback_matches_the_model() -> None:
    for field in ("draft_replies", "follow_up_auto_draft"):
        expected = str(getattr(AssistantSettingsModel(account_id="a"), field))
        assert _get_fallback(field) == expected, (
            f"/assistant/settings returns {field}={_get_fallback(field)} for an "
            f"account with no settings row, but the model defaults {expected}"
        )


# ── the agent path ──────────────────────────────────────────────────────────


def _load_agent():
    spec = importlib.util.spec_from_file_location("ea_auto_draft_t7", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_the_agent_saving_another_field_keeps_drafting_off(monkeypatch) -> None:
    """``update_assistant_settings`` reads the GET body and PUTs it back with the
    one field it was asked to change. On a new mailbox that body must carry
    drafting OFF, or saving the signature turns drafting on."""
    agents = _load_agent()
    get_body = await _get_without_row()
    sent: list[dict] = []

    async def fake_get(path, params=None):
        assert path == "/email/assistant/settings"
        return dict(get_body)

    async def fake_patch_settings(body):
        sent.append(body)
        return body

    monkeypatch.setattr(agents, "_get", fake_get)
    monkeypatch.setattr(agents, "_patch_settings", fake_patch_settings)
    await agents.update_assistant_settings("acc-1", signature="— Me")
    assert len(sent) == 1
    assert sent[0]["signature"] == "— Me"
    assert sent[0]["draft_replies"] is False
    assert AssistantSettingsModel.model_validate(sent[0]).draft_replies is False


# ── the stored choice that the preset seed reads ────────────────────────────


@pytest.mark.parametrize(("row", "expected"), [
    (None, False),                                   # no settings row
    (SimpleNamespace(draft_replies=None), False),    # NULL in the column
    (SimpleNamespace(draft_replies=False), False),
    (SimpleNamespace(draft_replies=True), True),     # the member turned it on
])
async def test_stored_draft_replies_is_off_unless_stored_true(row, expected) -> None:
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=row))
    assert await rules_mod.stored_draft_replies(db, "acc-1") is expected


# ── follow-up nudges: OFF ───────────────────────────────────────────────────


def test_follow_up_auto_draft_is_off_by_default() -> None:
    """Nudges threads by AGE, so it is the hundreds-at-once case, not the
    just-arrived one — and its scan was dead until #84, so the first working run
    on a long-configured account releases the whole window."""
    assert AssistantSettingsModel(account_id="acc-1").follow_up_auto_draft is False


def test_the_follow_up_column_default_ends_up_off() -> None:
    assert _column_default("follow_up_auto_draft") == "false"


# ── no migration rewrites a user's stored choice ────────────────────────────


def test_the_default_migrations_do_not_rewrite_existing_settings() -> None:
    """A stored value is a deliberate choice. Changing what NEW accounts inherit
    is the fix; silently flipping a switch a user set is not."""
    for path in (_MIGRATIONS / "81_auto_draft_defaults_off.sql",
                 _MIGRATIONS / "82_live_drafting_default_back_on.sql",
                 _migration_224()):
        # Comments are stripped first so the word "UPDATE" in the rationale
        # can't trip the check — a guard that fails on its own explanation just
        # gets the explanation deleted.
        body = "\n".join(
            ln for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("--")
        )
        assert "UPDATE" not in body.upper(), (
            f"{path.name} rewrites existing rows — it must only change the DEFAULT"
        )


def test_the_partial_insert_that_makes_column_defaults_matter_still_exists() -> None:
    """``derive_writing_style`` inserts only (account_id, writing_style), so it
    creates a settings row from column defaults. If this ever stops being true
    the migrations are still correct — but the reasoning above would be stale,
    so fail and make someone re-read it."""
    assert "(account_id, writing_style, updated_at)" in _ASSISTANT, (
        "the partial settings INSERT changed shape — re-check which paths can "
        "create a settings row from column defaults"
    )


# ── R8: migration 224 on a real database ────────────────────────────────────

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)


def _run_file(engine, path: Path | str) -> None:
    from tests.unit._tenant_ladder import _exec_file

    with engine.begin() as conn:
        _exec_file(conn, str(path))


def _settings(engine) -> dict[str, tuple[bool, bool]]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return {
            str(r.account_id): (r.draft_replies, r.follow_up_auto_draft)
            for r in conn.execute(text(
                "SELECT account_id, draft_replies, follow_up_auto_draft "
                "FROM email_assistant_settings"))
        }


def _column_defaults(engine) -> dict[str, str]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return {
            r.column_name: r.column_default
            for r in conn.execute(text(
                "SELECT column_name, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'email_assistant_settings' "
                "AND column_name IN ('draft_replies', 'follow_up_auto_draft')"))
        }


def _new_account(conn, label: str) -> str:
    from sqlalchemy import text

    aid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO email_accounts "
        "(id, user_id, provider, email_address, credentials_encrypted) "
        "VALUES (:id, :u, 'microsoft', :e, 'x')"),
        {"id": aid, "u": f"{label}@t7.test", "e": f"{label}@t7.test"})
    return aid


@pytest.fixture(scope="module")
def upgraded():
    """A private database: the ladder up to 224, three seeded rows, then the
    rest of the ladder from 224 on. Dropped at the end."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder

    admin_url = make_url(_URL)
    name = f"{admin_url.database}_emt7_{uuid.uuid4().hex[:8]}"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()

    eng = create_engine(admin_url.set(database=name), future=True)
    try:
        files = list(ladder())
        names = [os.path.basename(p) for p in files]
        cut = names.index(_migration_224().name)
        before, after = files[:cut], files[cut:]

        with eng.begin() as conn:
            _exec_file(conn, INIT_SCHEMA)
            for p in before:
                _exec_file(conn, p)

        with eng.begin() as conn:
            omitted = _new_account(conn, "omitted")
            stored_on = _new_account(conn, "stored-on")
            stored_off = _new_account(conn, "stored-off")
            # Like derive_writing_style: the column is not named.
            conn.execute(text(
                "INSERT INTO email_assistant_settings (account_id) VALUES (:a)"),
                {"a": omitted})
            conn.execute(text(
                "INSERT INTO email_assistant_settings (account_id, draft_replies) "
                "VALUES (:a, true)"), {"a": stored_on})
            conn.execute(text(
                "INSERT INTO email_assistant_settings (account_id, draft_replies) "
                "VALUES (:a, false)"), {"a": stored_off})
        before_224 = _settings(eng)

        for p in after:
            _run_file(eng, p)

        yield SimpleNamespace(
            engine=eng, omitted=omitted, stored_on=stored_on,
            stored_off=stored_off, before_224=before_224)
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


@_DB_GATE
class TestMigration224OnARealDatabase:
    def test_before_224_a_row_that_omits_the_column_got_true(self, upgraded) -> None:
        """The precondition: migration 82 was in force before 224, so the seed
        reproduces the state that 224 meets on an upgraded box."""
        assert upgraded.before_224[upgraded.omitted][0] is True

    def test_224_changes_no_existing_row(self, upgraded) -> None:
        rows = _settings(upgraded.engine)
        for aid in (upgraded.omitted, upgraded.stored_on, upgraded.stored_off):
            assert rows[aid] == upgraded.before_224[aid], aid
        assert rows[upgraded.omitted][0] is True
        assert rows[upgraded.stored_on][0] is True
        assert rows[upgraded.stored_off][0] is False

    def test_the_ladder_ends_with_both_defaults_false(self, upgraded) -> None:
        assert _column_defaults(upgraded.engine) == {
            "draft_replies": "false",
            "follow_up_auto_draft": "false",
        }

    def test_a_new_row_that_omits_the_columns_gets_false(self, upgraded) -> None:
        from sqlalchemy import text

        with upgraded.engine.begin() as conn:
            fresh = _new_account(conn, "fresh")
            conn.execute(text(
                "INSERT INTO email_assistant_settings (account_id, writing_style) "
                "VALUES (:a, 'short')"), {"a": fresh})
        assert _settings(upgraded.engine)[fresh] == (False, False)

    def test_a_second_run_changes_nothing(self, upgraded) -> None:
        rows = _settings(upgraded.engine)
        defaults = _column_defaults(upgraded.engine)
        _run_file(upgraded.engine, _migration_224())
        assert _settings(upgraded.engine) == rows
        assert _column_defaults(upgraded.engine) == defaults
