"""Automatic reply drafting is OFF for a new mailbox (D-EM-6).

    "Turn the default autodraft emails to off."             — owner, 2026-10-02

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.2 D-EM-6 and §10.4.9
(EM-T7). A member turns drafting on in AI settings. Every draft is a call on the
drafting model, so nobody should find it already running.

History. Migration 82 (2026-07-20) set live drafting ON, because a draft on mail
that just arrived was then the feature. D-EM-6 reverses that for a NEW mailbox.
Migration 224 changes no existing row.

Every path that can turn drafting on for a new mailbox is pinned here:

  * ``AssistantSettingsModel.draft_replies`` — a PUT that omits the field.
  * The GET with no settings row. The agent's ``update_assistant_settings``
    PUTs that body back, so an ON answer turned drafting on whenever the agent
    saved another field.
  * The column default — an INSERT that omits the column.
  * The Needs Reply preset — ``test_email_presets.py`` pins it.

Fix round 1 keeps the switch and the engine in agreement. A rule runs its own
DRAFT_EMAIL and never reads the setting. So with NO settings row the GET answers
what the reply rule does (``rules.py::reply_rule_drafts``). A new mailbox has no
rules and reads OFF. A mailbox from before D-EM-6 got DRAFT_EMAIL from the old
presets and reads ON. ``generate_writing_style`` stores the same answer when it
creates the first row. A stored row always wins.

``follow_up_auto_draft`` stays OFF (migration 81) and is pinned beside it. The
backfill is pinned by ``test_email_process_past_drafting.py``.

R8: the ``upgraded`` fixture builds a PRIVATE database on the server that
``TENANT_LADDER_DATABASE_URL`` names, applies the ladder up to 224, seeds rows,
applies the rest and drops the database. It never touches the shared ladder
database. The handler tests run the REAL handlers on it through asyncpg, the
driver of production.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_auto_draft_defaults.py -v -rs
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import rules as rules_mod
from gateway.routes.email.automation.assistant import AssistantSettingsModel

from tests.unit._email_fakes import bind_db
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = REPO / "infra/postgres"
_FIXTURE = json.loads(
    (REPO / "tests/fixtures/email_new_mailbox_settings.json")
    .read_text(encoding="utf-8")
)["get_without_settings_row"]
_AGENT = REPO / "apps/agents/agent-email-assistant/agents.py"
_ME = "me@acme.test"


def _migration_224() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    found = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if "D-EM-6" in p.read_text(encoding="utf-8")
        and "ALTER COLUMN draft_replies SET DEFAULT false"
        in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, f"expected one D-EM-6 default migration, got {found}"
    return found[0]


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


# ── a fake session that answers by table ────────────────────────────────────


def _settings_row(**over) -> SimpleNamespace:
    """A stored settings row, with every column the GET reads."""
    base = dict(
        about="", signature="", auto_run=True, cold_email_blocker="OFF",
        rule_model=None, draft_model=None, compose_model=None, chat_model=None,
        digest_frequency="OFF", personal_instructions="", writing_style="",
        learned_writing_style="", draft_replies=False, follow_up_days=0,
        draft_confidence="ALL_EMAILS", follow_up_awaiting_days=0,
        follow_up_needs_reply_days=0, follow_up_auto_draft=False,
        digest_categories=[], digest_day_of_week=1, digest_time_of_day="09:00",
        digest_send_to_email=True, morning_brief_enabled=False,
        multi_rule_execution=False, sensitive_data_protection=True,
        org_domains=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def _db(*, settings=None, drafts: bool = False, sent: list[str] | None = None):
    """``settings`` is the stored row (None means no row). ``drafts`` is what
    the reply-rule read answers. ``sent`` is the body text of sent mail."""
    def _exec(sql, params=None):
        s = str(sql)
        if hits(s, "FROM email_rules"):
            one, many = SimpleNamespace(drafts=drafts), []
        elif hits(s, "INSERT INTO email_assistant_settings"):
            one, many = SimpleNamespace(learned_writing_style=""), []
        elif hits(s, "FROM email_assistant_settings"):
            one, many = settings, []
        elif hits(s, "FROM email_accounts"):
            one, many = SimpleNamespace(email_address=_ME), []
        elif hits(s, "FROM email_messages"):
            one, many = None, [SimpleNamespace(body_text=b) for b in sent or []]
        else:
            raise AssertionError(f"unexpected SQL in this fake: {s[:80]}")
        return MagicMock(fetchone=MagicMock(return_value=one),
                         fetchall=MagicMock(return_value=many))

    db = AsyncMock()
    db.execute.side_effect = _exec
    return db


def _executed(db, clause: str) -> list[tuple[str, dict]]:
    return [
        (str(c.args[0]), c.args[1] if len(c.args) > 1 else {})
        for c in db.execute.call_args_list if hits(str(c.args[0]), clause)
    ]


async def _get(db) -> dict:
    """Run the REAL GET handler over ``db``."""
    with patch.object(assistant_mod, "_tenant_session", bind_db(db)), \
            patch.object(assistant_mod, "_assert_account_owner", AsyncMock()):
        return await assistant_mod.get_assistant_settings(
            account_id="acc-1", user=SimpleNamespace(email=_ME))


async def _put(db, body: dict) -> dict:
    """Run the REAL PUT handler over ``db``. The Needs Reply sync is a mock."""
    req = AssistantSettingsModel.model_validate({"account_id": "acc-1", **body})
    with patch.object(assistant_mod, "_tenant_session", bind_db(db)), \
            patch.object(assistant_mod, "_assert_account_owner", AsyncMock()), \
            patch.object(rules_mod, "sync_draft_reply_action",
                         AsyncMock(return_value=False)) as sync:
        res = await assistant_mod.put_assistant_settings(
            req=req, user=SimpleNamespace(email=_ME))
    res["_sync"] = sync
    return res


# ── live drafting: OFF for a new mailbox (D-EM-6) ───────────────────────────


def test_live_reply_drafting_is_off_by_default() -> None:
    """The model default is what a PUT without the field stores."""
    assert AssistantSettingsModel(account_id="acc-1").draft_replies is False


def test_the_live_column_default_ends_up_off() -> None:
    assert _column_default("draft_replies") == "false"


async def test_a_save_without_the_field_stores_false_and_adds_no_draft_action() -> None:
    """A body with no ``draft_replies`` goes through the REAL PUT handler. The
    INSERT binds false, and the Needs Reply sync is told to REMOVE drafting."""
    db = _db()
    res = await _put(db, {})
    (_, insert), = _executed(db, "INSERT INTO email_assistant_settings")
    assert insert["dr"] is False
    res["_sync"].assert_awaited_once_with(db, "acc-1", False)
    assert res["draft_replies"] is False


# ── the GET ─────────────────────────────────────────────────────────────────
# This is the state a member actually SEES. The AI settings toggle draws from
# it (the vitest half reads the same fixture), and the agent PUTs it back.


async def test_the_get_for_a_new_mailbox_answers_the_shared_fixture() -> None:
    """A new mailbox has no settings row and no rules."""
    body = await _get(_db(settings=None, drafts=False))
    assert {k: body[k] for k in _FIXTURE} == _FIXTURE
    # The line above holds the GET to the fixture, and the vitest half holds the
    # toggle to it. A fixture flipped to true would leave both halves agreeing
    # on ON, so these lines pin the decision itself (D-EM-6).
    assert _FIXTURE["draft_replies"] is False
    assert _FIXTURE["follow_up_auto_draft"] is False
    model = AssistantSettingsModel(account_id="a")
    assert body["draft_replies"] is model.draft_replies
    assert body["follow_up_auto_draft"] is model.follow_up_auto_draft


@pytest.mark.parametrize(("settings", "drafts", "expected"), [
    (None, False, False),   # a new mailbox: no row and no drafting rule
    (None, True, True),     # from before D-EM-6: the old Needs Reply drafts
    (_settings_row(draft_replies=False), True, False),  # stored wins
    (_settings_row(draft_replies=True), False, True),   # stored wins
], ids=["new", "legacy", "stored-off-wins", "stored-on-wins"])
async def test_the_get_answers_what_the_engine_does(settings, drafts, expected) -> None:
    body = await _get(_db(settings=settings, drafts=drafts))
    assert body["draft_replies"] is expected


async def test_a_stored_row_skips_the_rule_read() -> None:
    db = _db(settings=_settings_row(draft_replies=True))
    await _get(db)
    assert _executed(db, "FROM email_rules") == []


# ── the PUT answer ──────────────────────────────────────────────────────────
# SettingsTab keeps the PUT answer as its state and PUTs it back on the next
# change. A key the answer drops is stored as its model default at that save.
# morning_brief_enabled turned itself off that way.


async def test_the_put_answer_carries_every_get_key() -> None:
    get_body = await _get(_db())
    put_body = await _put(_db(), get_body)
    put_body.pop("_sync")
    assert set(put_body) == set(get_body), (
        f"only in GET: {sorted(set(get_body) - set(put_body))}, "
        f"only in PUT: {sorted(set(put_body) - set(get_body))}"
    )


async def test_the_put_answer_keeps_morning_brief_enabled() -> None:
    res = await _put(_db(), {"morning_brief_enabled": True})
    assert res["morning_brief_enabled"] is True


# ── generate_writing_style can create the first settings row ────────────────


async def _generate(db) -> None:
    memory = SimpleNamespace(add_memories_background=AsyncMock())
    with patch.object(assistant_mod, "_tenant_session", bind_db(db)), \
            patch.object(assistant_mod, "_assert_account_owner", AsyncMock()), \
            patch.object(assistant_mod, "_llm_writing_style",
                         AsyncMock(return_value="Short and direct.")), \
            patch.dict(sys.modules, {"acb_memory": memory}):
        await assistant_mod.generate_writing_style(
            account_id="acc-1", user=SimpleNamespace(email=_ME))


@pytest.mark.parametrize("drafts", [False, True], ids=["new", "legacy"])
async def test_a_new_row_from_the_style_stores_what_the_engine_does(drafts) -> None:
    db = _db(drafts=drafts, sent=["Thanks, will do."])
    await _generate(db)
    (sql, params), = _executed(db, "INSERT INTO email_assistant_settings")
    assert params["dr"] is drafts
    # A stored choice stays: the DO UPDATE must not name the column.
    assert "draft_replies" not in sql.split("DO UPDATE SET", 1)[1]


def test_the_style_insert_names_draft_replies() -> None:
    """The column default still binds every other INSERT that omits the column.
    This one names it, so the default never decides a legacy mailbox."""
    src = inspect.getsource(assistant_mod.generate_writing_style)
    assert "(account_id, writing_style, draft_replies, updated_at)" in src


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
    get_body = await _get(_db())
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


def test_the_get_follow_up_fallback_matches_the_model() -> None:
    src = inspect.getsource(assistant_mod.get_assistant_settings)
    m = re.search(
        r'"follow_up_auto_draft": \(\s*bool\(.*?\)\s*if .*?else (True|False)\s*\)',
        src, re.DOTALL,
    )
    assert m, "could not find the GET fallback for follow_up_auto_draft"
    assert m.group(1) == str(AssistantSettingsModel(account_id="a").follow_up_auto_draft)


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


# ── R8: a real database ─────────────────────────────────────────────────────

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


def _new_account(conn, label: str, org: str | None = None) -> str:
    """A mailbox owned by ``<label>@t7.test``. ``org`` puts it in a tenant."""
    from sqlalchemy import text

    aid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO email_accounts "
        "(id, user_id, provider, email_address, credentials_encrypted, "
        " organization_id) "
        "VALUES (:id, :u, 'microsoft', :e, 'x', :org)"),
        {"id": aid, "u": f"{label}@t7.test", "e": f"{label}@t7.test", "org": org})
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
            # An INSERT that does not name the column.
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


# ── R8: the real handlers on the private database ───────────────────────────


def _real_session(engine):
    """A ``_tenant_session`` double over a REAL asyncpg connection.

    It opens one transaction and commits it on a clean exit, as the seam does.
    The role is the ladder owner, so this proves the SQL and the account filter,
    not RLS. ``test_email_tenant_bind_rls.py`` owns RLS.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    url = engine.url.set(drivername="postgresql+asyncpg")

    @asynccontextmanager
    async def _tenant_session():
        aeng = create_async_engine(url)
        try:
            async with aeng.connect() as conn, conn.begin():
                yield conn
        finally:
            await aeng.dispose()

    return _tenant_session


def _seed(engine):
    """A helper that seeds one tenant on the private database."""
    from sqlalchemy import text

    class _Seed:
        def __init__(self) -> None:
            with engine.begin() as conn:
                self.org = str(conn.execute(text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES (:s, :s) RETURNING id"),
                    {"s": f"t7-{uuid.uuid4().hex[:10]}"}).scalar())

        def mailbox(self) -> tuple[str, str]:
            label = f"m-{uuid.uuid4().hex[:10]}"
            with engine.begin() as conn:
                aid = _new_account(conn, label, self.org)
            return aid, f"{label}@t7.test"

        def rule(self, aid: str, name: str, types: list[str],
                 system_type: str | None = None) -> None:
            with engine.begin() as conn:
                rid = conn.execute(text(
                    "INSERT INTO email_rules (account_id, name, system_type) "
                    "VALUES (:a, :n, :st) RETURNING id"),
                    {"a": aid, "n": name, "st": system_type}).scalar()
                for t in types:
                    conn.execute(text(
                        "INSERT INTO email_actions (rule_id, type, label) "
                        "VALUES (:r, :t, :n)"), {"r": rid, "t": t, "n": name})

        def store(self, aid: str, **cols) -> None:
            names = ", ".join(["account_id", *cols])
            binds = ", ".join([":account_id", *(f":{c}" for c in cols)])
            with engine.begin() as conn:
                conn.execute(text(
                    f"INSERT INTO email_assistant_settings ({names}) "
                    f"VALUES ({binds})"), {"account_id": aid, **cols})

        def sent(self, aid: str, body: str) -> None:
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO email_messages (account_id, provider_message_id, "
                    " folder, from_address, to_addresses, body_text) "
                    "VALUES (:a, :p, 'Sent', '{}'::jsonb, '[]'::jsonb, :b)"),
                    {"a": aid, "p": uuid.uuid4().hex, "b": body})

    return _Seed()


async def _real_get(engine, aid: str, email: str) -> dict:
    with patch.object(assistant_mod, "_tenant_session", _real_session(engine)):
        return await assistant_mod.get_assistant_settings(
            account_id=aid, user=SimpleNamespace(email=email))


@_DB_GATE
class TestTheSwitchOnARealDatabase:
    """The GET with no settings row answers what the reply rule does. The
    owner check runs for real, so each call names the owner of the mailbox."""

    async def test_no_row_and_no_rules_reads_off(self, upgraded) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is False

    async def test_no_row_and_a_needs_reply_rule_that_drafts_reads_on(self, upgraded) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.rule(aid, "Needs Reply", ["LABEL", "DRAFT_EMAIL"])
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is True

    async def test_no_row_and_a_needs_reply_rule_that_labels_only_reads_off(
        self, upgraded,
    ) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.rule(aid, "Needs Reply", ["LABEL"])
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is False

    async def test_a_reply_system_type_counts_under_any_name(self, upgraded) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.rule(aid, "Answer these", ["DRAFT_EMAIL"], system_type="TO_REPLY")
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is True

    async def test_a_draft_on_another_rule_does_not_count(self, upgraded) -> None:
        """Only the reply rule is the switch. A custom rule can draft too, and
        the switch does not govern it."""
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.rule(aid, "Newsletter", ["LABEL", "DRAFT_EMAIL"])
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is False

    async def test_a_stored_false_wins_over_a_rule_that_drafts(self, upgraded) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.rule(aid, "Needs Reply", ["LABEL", "DRAFT_EMAIL"])
        s.store(aid, draft_replies=False)
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is False

    async def test_a_stored_true_wins_with_no_rules(self, upgraded) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.store(aid, draft_replies=True)
        assert (await _real_get(upgraded.engine, aid, me))["draft_replies"] is True

    async def test_the_rule_read_is_per_mailbox(self, upgraded) -> None:
        """Two mailboxes in one tenant. Only the second has a drafting rule."""
        s = _seed(upgraded.engine)
        plain, me_plain = s.mailbox()
        legacy, me_legacy = s.mailbox()
        s.rule(legacy, "Needs Reply", ["LABEL", "DRAFT_EMAIL"])
        assert (await _real_get(upgraded.engine, plain, me_plain))["draft_replies"] is False
        assert (await _real_get(upgraded.engine, legacy, me_legacy))["draft_replies"] is True

    async def test_stored_draft_replies_reads_each_mailbox_alone(self, upgraded) -> None:
        """Two mailboxes in one tenant: one stored true, one with no row. The
        hermetic fakes answer one row for every statement, so only this test
        fences ``WHERE account_id = :aid``."""
        s = _seed(upgraded.engine)
        on, _ = s.mailbox()
        none, _ = s.mailbox()
        s.store(on, draft_replies=True)
        async with _real_session(upgraded.engine)() as conn:
            assert await rules_mod.stored_draft_replies(conn, on) is True
            assert await rules_mod.stored_draft_replies(conn, none) is False

    async def test_the_put_answer_carries_every_get_key_and_keeps_the_brief(
        self, upgraded,
    ) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        s.store(aid, learned_writing_style="Drops pleasantries.",
                morning_brief_enabled=False)
        get_body = await _real_get(upgraded.engine, aid, me)
        req = AssistantSettingsModel.model_validate(
            {**get_body, "morning_brief_enabled": True})
        with patch.object(assistant_mod, "_tenant_session",
                          _real_session(upgraded.engine)):
            put_body = await assistant_mod.put_assistant_settings(
                req=req, user=SimpleNamespace(email=me))
        assert set(put_body) == set(get_body)
        assert put_body["morning_brief_enabled"] is True
        assert put_body["learned_writing_style"] == "Drops pleasantries."
        again = await _real_get(upgraded.engine, aid, me)
        assert again["morning_brief_enabled"] is True

    @pytest.mark.parametrize(("legacy", "stored", "expected"), [
        (False, None, False),   # a new mailbox: the row gets false
        (True, None, True),     # from before D-EM-6: the row keeps drafting
        (True, False, False),   # a stored choice stays
    ], ids=["new", "legacy", "stored-stays"])
    async def test_the_style_row_stores_what_the_engine_does(
        self, upgraded, legacy, stored, expected,
    ) -> None:
        s = _seed(upgraded.engine)
        aid, me = s.mailbox()
        if legacy:
            s.rule(aid, "Needs Reply", ["LABEL", "DRAFT_EMAIL"])
        if stored is not None:
            s.store(aid, draft_replies=stored)
        s.sent(aid, "Thanks, will do.")
        memory = SimpleNamespace(add_memories_background=AsyncMock())
        with patch.object(assistant_mod, "_tenant_session",
                          _real_session(upgraded.engine)), \
                patch.object(assistant_mod, "_llm_writing_style",
                             AsyncMock(return_value="Short and direct.")), \
                patch.dict(sys.modules, {"acb_memory": memory}):
            await assistant_mod.generate_writing_style(
                account_id=aid, user=SimpleNamespace(email=me))
        assert _settings(upgraded.engine)[aid][0] is expected
        body = await _real_get(upgraded.engine, aid, me)
        assert body["draft_replies"] is expected
        assert body["writing_style"] == "Short and direct."
