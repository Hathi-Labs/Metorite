"""CRM · auto-lead from inbound email (WS-26d-autolead).

Spec: ``project-docs/specs/crm_app.md`` §9 ``WS-26d-autolead`` —
done-when 1-7, each named in a test below · D-CRM-9 · D-CRM-12.

**What this file is defending.** With ``CRM_AUTO_LEAD`` on, a scheduler hook
writes CRM records with no human in the loop, and by D-CRM-9 each one is born
``zoho_dirty`` and reaches the LIVE Zoho tenant within one 600s sync cycle.
There is no confirmation card on a scheduler hook and there is no delete tool.
So the tests that matter are the ones about what it must NOT do:

* ``test_dw2_*`` — with the flag off the step is not entered. Asserted twice:
  once at runtime (a sentinel that raises if it is called) and once
  structurally (the call is lexically inside an ``if auto_lead_enabled()``), so
  a future refactor that moves the gate INSIDE the step fails here rather than
  quietly opening a database session per mailbox per cycle.
* ``test_dw7_*`` — a deep resync of a newly connected mailbox mints NOTHING.
  ``process_new_mail`` is reached by ~1-year backfills that stamp no
  held-back marker, so "everything classified" would mint a lead per unknown
  sender across a year of mail.
* ``test_dw5_*`` — colleague, self and already-known senders create nothing.
  Two colleague cases, because there are two gates and they answer different
  questions: same-domain is ``sender_scope``'s, a configured second org domain
  is the internal-domain list's.
* ``test_dw6_*`` — the lead IS born ``zoho_dirty`` (that is the design, not an
  accident), and its first activity's ``type='system'`` is asserted absent from
  ``sync_zoho``'s own push statement rather than from a comment about it.

Hermetic, in ``test_crm_routes.py``'s convention: no Postgres, no network, no
TestClient; the step is called directly with ``_tenant_session`` monkeypatched
onto the SUT submodules against the shared ``_crm_fakes.FakeCrmDB``, and with
the mailbox's tenant bound, as the email hook binds it (WS-53 CRM-T3). The real
settings object is never mutated — the ON state is the module's own
``auto_lead_enabled`` predicate, monkeypatched, exactly as
``test_crm_zoho_sync.py`` does for ``sync_enabled``.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.crm import auto_lead
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm import sync_zoho as crm_sync
from gateway.routes.email import scheduler_hooks

from tests.unit._crm_fakes import FakeCrmDB, bind_db

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "infra" / "postgres"
HOOKS = REPO / "apps/services/gateway/gateway/routes/email/scheduler_hooks.py"

#: One mailbox: `vjvarada@fracktal.in`, owned by the same person.
#: `email_accounts.user_id` holds the owner's EMAIL — see
#: `routes/email/core._account_scope`, which compares it to one.
ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
OWNER = "vjvarada@fracktal.in"

STRANGER = "asha@acmerobotics.com"


def _at(day: int, hour: int = 9, *, year: int = 2026) -> datetime:
    return datetime(year, 8, day, hour, 0, tzinfo=UTC)


#: When auto-lead became active on this account, in every test that does not
#: care about activation itself.
ACTIVATED = _at(1)


# ── Fixtures ────────────────────────────────────────────────────────────────

#: The tenant the email hook binds before it runs the step. The fake does not
#: model RLS. The R8 suite ``test_crm_sync_tenancy.py`` does.
MAILBOX_ORG = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCrmDB]:
    """The in-memory schema, bound to every module that opens a session.

    ``auto_lead`` is in the tuple because it imports ``_tenant_session`` from
    ``core`` by name; ``records`` is in it because ``create_record`` opens its
    OWN session and the lead has to land in the same fake. The mailbox's
    tenant is bound for the test, because with none bound the step opens no
    session at all (WS-53 CRM-T3).
    """
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake,
            (crm_core, crm_records, crm_pipeline, auto_lead))
    token = bind_tenant(MAILBOX_ORG)
    try:
        yield fake
    finally:
        release_tenant(token)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ON state, without touching the real settings object.

    Monkeypatching the module's own predicate is the pattern
    ``test_crm_zoho_sync.py`` uses for ``CRM_ZOHO_SYNC``: flipping the shared
    ``get_settings()`` singleton leaks an owner-gated flag into every test that
    runs afterwards.
    """
    monkeypatch.setattr(auto_lead, "auto_lead_enabled", lambda: True)


class _Log:
    """A structlog stand-in that records (level, key, kwargs).

    The stall and the re-anchor are *log lines by design* — WARNING, repeating
    every cycle, because the state they report is invisible in the row counts
    (a held cursor and a quiet mailbox both create nothing). A test that only
    checked the counters would let the line be deleted.
    """

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, key: str, **kwargs: Any) -> None:
        self.lines.append(("info", key, kwargs))

    def warning(self, key: str, **kwargs: Any) -> None:
        self.lines.append(("warning", key, kwargs))

    def at(self, level: str, key: str) -> list[dict[str, Any]]:
        return [kw for lvl, k, kw in self.lines if lvl == level and k == key]

    def keys(self) -> list[str]:
        return [key for _lvl, key, _kw in self.lines]


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> _Log:
    recorder = _Log()
    monkeypatch.setattr(auto_lead, "_log", recorder)
    return recorder


class _Clock:
    """A movable clock for the step's ``now()``.

    The dormancy tests are about ELAPSED TIME BETWEEN INVOCATIONS, and the
    honest way to express "nine hours passed and the step was never called" is
    to move the clock and not call it — not to hand-seed a ``last_run_at`` the
    scheduler could never have produced. The first version of these tests did
    exactly that and was green on a state that cannot occur.
    """

    def __init__(self, start: datetime) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> datetime:
        self.at = self.at + timedelta(**delta)
        return self.at


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Starts at 22:00 — the beginning of the overnight lull these tests are
    about."""
    movable = _Clock(datetime(2026, 8, 7, 22, 0, tzinfo=UTC))
    monkeypatch.setattr(auto_lead, "now", movable)
    return movable


@pytest.fixture
def quiet_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the five mail steps ``process_new_mail`` runs before ours.

    They are each try/except-isolated, so leaving them live would not fail the
    test — it would make it spend its time opening real database connections
    to discover that.
    """
    from gateway.routes.email.automation import cleanup, replyzero, senders

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(scheduler_hooks, "auto_run_rules_for_account", _noop)
    monkeypatch.setattr(cleanup, "sweep_uncategorized", _noop)
    monkeypatch.setattr(senders, "_categorize_senders_job", _noop)
    monkeypatch.setattr(senders, "_maybe_auto_archive", _noop)
    monkeypatch.setattr(replyzero, "_maybe_classify_threads", _noop)


# ── Seeding ─────────────────────────────────────────────────────────────────

def _seed_account(
    db: FakeCrmDB, *, owner: str = OWNER, address: str = OWNER,
    org_domains: list[str] | None = None,
) -> None:
    # The mailbox belongs to the tenant the `db` fixture binds. `_load_account`
    # filters on it (RLS on `email_accounts` is not on prod yet).
    db.seed("email_accounts", id=ACCOUNT_ID, user_id=owner,
            email_address=address, organization_id=MAILBOX_ORG)
    if org_domains is not None:
        db.seed("email_assistant_settings", account_id=ACCOUNT_ID,
                org_domains=org_domains)


def _seed_status(db: FakeCrmDB) -> Any:
    """The lane a new lead lands in — ``_resolve_status`` requires one."""
    return db.seed("crm_lead_statuses", name="New", is_default=True,
                   position=0, type="open")


def _seed_cursor(
    db: FakeCrmDB, *, activated_at: datetime = ACTIVATED,
    watermark: datetime | None = None, last_run_at: datetime | None = None,
) -> Any:
    """An account auto-lead is already active on.

    ``last_run_at`` defaults to NOW, not to ``activated_at``: the ordinary
    state of a running account is "the step ran a moment ago", and seeding it
    stale is how a test asks for the dormancy path.
    """
    return db.seed("crm_auto_lead_cursors", account_id=ACCOUNT_ID,
                   activated_at=activated_at,
                   processed_watermark=watermark or activated_at,
                   last_run_at=last_run_at or datetime.now(UTC))


#: "you did not say" — distinct from an explicit ``None``, which is how a test
#: seeds mail the rules have NOT classified. ``processed_at=None`` defaulting
#: to ``received_at`` would silently classify the one message that must not be.
_UNSET = object()


def _message(
    db: FakeCrmDB, *, address: str = STRANGER, name: str = "Asha Menon",
    received_at: datetime | None = None, processed_at: Any = _UNSET,
    subject: str = "Quote for 40 printers", folder: str = "INBOX",
    held_back_at: datetime | None = None, thread_id: str = "t-1",
    body_text: str = "SECRET BODY — must never leave the mailbox",
) -> Any:
    received_at = received_at or _at(5)
    if processed_at is _UNSET:
        processed_at = received_at
    return db.seed(
        "email_messages", account_id=ACCOUNT_ID, folder=folder,
        from_address={"name": name, "email": address},
        to_addresses=[{"email": OWNER}], subject=subject,
        body_text=body_text, snippet=body_text[:40],
        received_at=received_at,
        rules_processed_at=processed_at,
        rules_held_back_at=held_back_at,
        thread_id=thread_id, internet_message_id="<msg-1@acmerobotics.com>",
    )


def _ready(db: FakeCrmDB) -> None:
    """The steady state: an activated account with a pipeline lane."""
    _seed_account(db)
    _seed_status(db)
    _seed_cursor(db)


def _leads(db: FakeCrmDB) -> list[dict[str, Any]]:
    return db.rows("crm_leads")


def _activities(db: FakeCrmDB) -> list[dict[str, Any]]:
    return db.rows("crm_activities")


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    """``meta`` as the route bound it — jsonb rides the wire as text."""
    value = row.get("meta")
    return json.loads(value) if isinstance(value, str) else (value or {})


# ── done-when 1: the flag exists and ships OFF ──────────────────────────────

def test_dw1_the_flag_ships_off() -> None:
    from acb_common import get_settings

    assert get_settings().crm_auto_lead is False
    assert auto_lead.auto_lead_enabled() is False


def test_dw1_the_flag_is_a_settings_field_not_org_settings() -> None:
    """``acb_common/settings.py``, beside ``crm_zoho_sync`` — the precedent
    shape for a flag whose flip is an owner gate. An `org_settings` row would
    put an OWNER-GATE flip behind an admin UI."""
    from acb_common.settings import Settings

    assert "crm_auto_lead" in Settings.model_fields
    assert Settings.model_fields["crm_auto_lead"].default is False


def test_dw1_the_flag_is_not_written_into_the_env_example() -> None:
    """Deliberate absence. ``.env.example`` is plan-guard territory; the flag
    is documented in ``settings.py`` and ``crm_app.md`` and nowhere else."""
    example = (REPO / ".env.example")
    if not example.exists():  # pragma: no cover — it is committed
        pytest.skip(".env.example is absent from this checkout")
    assert "CRM_AUTO_LEAD" not in example.read_text(encoding="utf-8").upper()


# ── done-when 2: the OFF state never enters the step ────────────────────────

async def test_dw2_with_the_flag_off_the_step_is_not_entered(
    db: FakeCrmDB, quiet_pipeline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag ships off, so this is the DEFAULT behaviour of every sync.

    Asserted as "the step was never called", not as "the step created
    nothing": a gate that lived inside `create_leads_from_new_mail` would pass
    the second assertion while still opening a session per mailbox per cycle.
    """
    calls: list[str] = []

    async def _sentinel(account_id: str) -> dict[str, int]:
        calls.append(account_id)
        raise AssertionError("the CRM step ran with CRM_AUTO_LEAD off")

    monkeypatch.setattr(auto_lead, "create_leads_from_new_mail", _sentinel)
    _ready(db)
    _message(db, received_at=_at(5))

    await scheduler_hooks.process_new_mail(ACCOUNT_ID)

    assert calls == []
    assert db.statements == []
    assert _leads(db) == []


async def test_dw2_with_the_flag_on_the_step_is_entered(
    db: FakeCrmDB, on: None, quiet_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for the test above: without this, "never called" would also
    be satisfied by a hook that lost the call entirely."""
    calls: list[str] = []

    async def _recorder(account_id: str) -> dict[str, int]:
        calls.append(account_id)
        return {}

    monkeypatch.setattr(auto_lead, "create_leads_from_new_mail", _recorder)

    await scheduler_hooks.process_new_mail(ACCOUNT_ID)

    assert calls == [ACCOUNT_ID]


def _process_new_mail_ast() -> ast.AsyncFunctionDef:
    tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name == "process_new_mail"):
            return node
    raise AssertionError("process_new_mail is gone from scheduler_hooks.py")


def _calls_named(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == name
        for child in ast.walk(node)
    )


def test_dw2_the_gate_is_lexically_outside_the_step() -> None:
    """Structural, because the runtime test above cannot see a refactor.

    The call to ``create_leads_from_new_mail`` must sit inside an ``if`` whose
    test calls ``auto_lead_enabled``. Moving the flag check into the step —
    the exact short-circuit done-when 2 names — leaves the runtime sentinel
    unhappy and this assertion is what says WHY.
    """
    function = _process_new_mail_ast()
    guarded = [
        branch for branch in ast.walk(function)
        if isinstance(branch, ast.If)
        and _calls_named(branch.test, "auto_lead_enabled")
        and _calls_named(branch, "create_leads_from_new_mail")
    ]
    assert guarded, (
        "process_new_mail no longer calls create_leads_from_new_mail from "
        "inside `if auto_lead_enabled():` — with the flag off the step is "
        "entered, which is the short-circuit done-when 2 forbids"
    )
    # …and nowhere else: one guarded call site, not a guarded one plus a
    # forgotten unguarded one.
    total = sum(
        1 for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "create_leads_from_new_mail"
    )
    assert total == 1


def test_dw2_the_step_is_not_even_imported_when_the_flag_is_off() -> None:
    """The import of the STEP lives inside the guarded branch too.

    Not cosmetic: `from gateway.routes.crm.auto_lead import
    create_leads_from_new_mail` above the gate would run on every mail cycle
    of every mailbox with the flag off. What must stay above the gate is the
    predicate — `auto_lead_enabled` is the flag's one definition, and reading
    `settings.crm_auto_lead` here instead would make two places responsible
    for agreeing what the flag means.
    """
    function = _process_new_mail_ast()
    branches = [
        branch for branch in ast.walk(function)
        if isinstance(branch, ast.If)
        and _calls_named(branch.test, "auto_lead_enabled")
    ]
    assert branches, "the flag gate is gone"
    imported_inside = {
        alias.name
        for branch in branches
        for node in ast.walk(branch)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "create_leads_from_new_mail" in imported_inside, (
        "the step is imported above the flag gate — routes/crm loads on every "
        "mail cycle even with CRM_AUTO_LEAD off"
    )
    assert "auto_lead_enabled" not in imported_inside, (
        "the predicate cannot be imported inside its own gate"
    )


def test_dw2_a_crm_failure_never_breaks_mail_sync(
    on: None, quiet_pipeline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape every sibling step in ``process_new_mail`` has (`:81-112`):
    the CRM is the newest and least important thing on this path."""
    source = inspect.getsource(scheduler_hooks.process_new_mail)
    assert "sync.auto_lead_failed" in source, (
        "the CRM step's failure log key must follow the sync.*_failed "
        "convention its five siblings use"
    )


async def test_dw2_a_raising_step_is_swallowed_and_logged(
    on: None, quiet_pipeline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str) -> dict[str, int]:
        raise RuntimeError("crm exploded")

    monkeypatch.setattr(auto_lead, "create_leads_from_new_mail", _boom)

    # No exception escapes: mail sync must complete even when the CRM does not.
    await scheduler_hooks.process_new_mail(ACCOUNT_ID)


def test_dw2_the_crm_step_runs_after_every_mail_step() -> None:
    """Order is a decision: the step considers what is STILL in the inbox once
    the account's own automation has finished, so mail the user's own rules
    archived never becomes a lead."""
    source = inspect.getsource(scheduler_hooks.process_new_mail)
    positions = [
        source.index(step) for step in (
            "auto_run_rules_for_account(", "sweep_uncategorized(",
            "_categorize_senders_job(", "_maybe_classify_threads(",
            "_maybe_auto_archive(", "create_leads_from_new_mail(",
        )
    ]
    assert positions == sorted(positions)


# ── done-when 3: the ON-state mint ──────────────────────────────────────────

async def test_dw3_an_unknown_external_sender_becomes_exactly_one_lead(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db, received_at=_at(5))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 1
    assert stats["created"] == 1
    rows = _leads(db)
    assert len(rows) == 1
    assert rows[0]["email"] == STRANGER
    assert rows[0]["source"] == "email"
    assert rows[0]["owner_email"] == OWNER
    assert rows[0]["lead_name"] == "Asha Menon"
    assert rows[0]["status_id"]  # `_resolve_status` ran — NOT NULL satisfied


async def test_a_mailbox_of_another_org_makes_no_lead_without_rls(
    db: FakeCrmDB, on: None,
) -> None:
    """The fake has no RLS, like prod's `email_accounts` today. A mailbox of
    another org must still make no lead in the bound org: `_load_account`
    filters on `organization_id` by itself (CRM-T3 review, P2)."""
    db.seed("email_accounts", id=ACCOUNT_ID, user_id=OWNER,
            email_address=OWNER,
            organization_id="88888888-8888-8888-8888-888888888888")
    _seed_status(db)
    _seed_cursor(db)
    _message(db, received_at=_at(5))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 0
    assert stats["created"] == 0
    assert _leads(db) == []


async def test_dw3_the_display_name_is_stripped_before_it_is_split(
    db: FakeCrmDB, on: None,
) -> None:
    """The "Asha Asha" trap (§8 B-series), in its inbound form: a quoted,
    padded display name split without stripping produces a first name of
    ``'"Asha'`` and a surname of ``'Menon"'``."""
    _ready(db)
    _message(db, name='  "Asha Menon"  ')

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    row = _leads(db)[0]
    assert row["first_name"] == "Asha"
    assert row["last_name"] == "Menon"
    assert row["lead_name"] == "Asha Menon"


async def test_dw3_a_one_word_display_name_is_not_doubled(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db, name="Asha")

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    row = _leads(db)[0]
    assert row["first_name"] == "Asha"
    assert row.get("last_name") is None
    assert row["lead_name"] == "Asha"


async def test_dw3_a_display_name_that_is_an_address_falls_through(
    db: FakeCrmDB, on: None,
) -> None:
    """`compute_lead_name`'s chain ends at the email local part, which is why
    a sender with no display name cannot 422 (`LEADS.required` is empty). A
    lead called `noreply@vendor.com` is what building the name by hand gives
    you."""
    _ready(db)
    _message(db, address="noreply@vendor.com", name="noreply@vendor.com")

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    row = _leads(db)[0]
    assert row.get("first_name") is None
    assert row["lead_name"] == "noreply"


async def test_dw3_the_lead_goes_through_the_service_write_path(
    db: FakeCrmDB, on: None,
) -> None:
    """`records.create_record`, never raw SQL. Four things live only there —
    `_resolve_status`, the `owner_email` default, `validate_source` and
    `mark_dirty_on_insert` — and a hand-rolled INSERT loses all four while
    only the last is visible in the row."""
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    source = inspect.getsource(auto_lead)
    assert "INSERT INTO crm_leads" not in source, (
        "the step writes crm_leads directly — that path has no _resolve_status,"
        " no owner default, no validate_source and no dirty marking"
    )
    assert "create_record(" in source
    row = _leads(db)[0]
    assert row["status_id"] and row["owner_email"] and row["lead_name"]


async def test_dw3_the_first_activity_is_metadata_never_content(
    db: FakeCrmDB, on: None,
) -> None:
    """D-CRM-12 applied to what a machine writes: the lead row is org-visible
    to every `feature:crm` holder and goes to Zoho; sender + subject is the
    proportionate disclosure for a cold inquiry, the body is not."""
    _ready(db)
    _message(db, subject="Quote for 40 printers", received_at=_at(5, 11))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert len(_activities(db)) == 1
    activity = _activities(db)[0]
    assert activity["type"] == "system"
    assert activity["subject"] == "Quote for 40 printers"
    assert activity["body"] is None
    assert activity["lead_id"] == _leads(db)[0]["id"]
    assert activity["created_by"] == OWNER
    meta = _meta(activity)
    assert meta["sender_address"] == STRANGER
    assert meta["sender_name"] == "Asha Menon"
    assert meta["thread_id"] == "t-1"
    assert meta["received_at"].startswith("2026-08-05")
    assert meta["message_id"]


async def test_dw3_no_body_or_snippet_is_ever_read(
    db: FakeCrmDB, on: None,
) -> None:
    """The privacy boundary is the PROJECTION: nothing downstream can leak a
    body it was never handed. Asserted against every statement the cycle
    issued, not only against the candidate query's source."""
    _ready(db)
    _message(db, body_text="wire transfer details, account 1234")

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    for statement in db.statements:
        assert "body_text" not in statement
        assert "snippet" not in statement
    stored = json.dumps(db.tables.get("crm_activities", []), default=str)
    assert "wire transfer" not in stored


async def test_dw3_only_classified_inbox_mail_is_a_candidate(
    db: FakeCrmDB, on: None,
) -> None:
    """Four disqualifications, one per predicate — each one is mail that must
    not mint a lead even though it arrived after activation."""
    _ready(db)
    _message(db, address="unclassified@a.com", processed_at=None)
    _message(db, address="heldback@b.com", held_back_at=_at(5))
    _message(db, address="sentmail@c.com", folder="SENT")
    _message(db, address="archived@d.com", folder="ARCHIVE")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 0
    assert _leads(db) == []


# ── done-when 4: idempotency and in-batch de-duplication ────────────────────

async def test_dw4_re_running_the_same_sync_creates_no_second_lead(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert len(_leads(db)) == 1
    assert len(_activities(db)) == 1


async def test_dw4_the_watermark_means_the_second_run_reconsiders_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """The sharper half of idempotency, and the one that fails when the
    watermark stops advancing: "one lead" would still hold — the third
    unknown-sender step would find the lead the first run created — while the
    step re-read, re-classified and re-probed the same mail forever."""
    _ready(db)
    _message(db, processed_at=_at(5))

    first = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    second = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert first["candidates"] == 1
    assert second["candidates"] == 0
    cursor = db.rows("crm_auto_lead_cursors")[0]
    assert cursor["processed_watermark"] == _at(5)
    assert cursor["activated_at"] == ACTIVATED  # never advanced


async def test_dw4_one_sender_emailing_twice_in_a_batch_mints_one_lead(
    db: FakeCrmDB, on: None,
) -> None:
    """In-batch de-duplication, asserted so that deleting it goes red.

    "One lead" alone would not: `create_record` commits on another session and
    the third unknown-sender step would find that lead. What only the dedup
    buys is that the second message is never CONSIDERED — one probe per
    address, not one per message — and in production, where those two sessions
    are genuinely concurrent, that is the difference between one lead and two.
    """
    _ready(db)
    _message(db, processed_at=_at(5, 9), thread_id="t-1")
    _message(db, processed_at=_at(5, 10), thread_id="t-2",
             subject="Following up")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 2
    assert stats["created"] == 1
    assert stats["deduped_in_batch"] == 1
    assert len(_leads(db)) == 1
    probes = db.statements_touching("FROM crm_leads WHERE lower(email)")
    assert len(probes) == 1, (
        "the second message from the same sender was probed again — in-batch "
        "de-duplication is gone and only the fake's shared dict is hiding it"
    )


# ── done-when 5: the three nothing-cases ────────────────────────────────────

async def test_dw5_a_colleague_on_the_accounts_own_domain_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """Gate 1 — ``sender_scope`` answers "internal" for the owner's own
    domain. A lead row for your own CFO, pushed into the live Zoho tenant, is
    the failure this pair of gates exists to prevent."""
    _ready(db)
    _message(db, address="cfo@fracktal.in", name="Our CFO")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_internal"] == 1
    assert _leads(db) == []


async def test_dw5_a_colleague_on_a_configured_org_domain_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """Gate 2 — and the case that proves it is not a restatement of gate 1.

    The org domain was typed as an ADDRESS, which is what people do.
    ``resolve_org_domains`` runs ``normalize_domain`` over it and gets
    ``fracktalworks.com``; ``sender_scope``'s own extra-domain arm only
    ``lstrip('@')``s and gets ``ops@fracktalworks.com``, which matches no
    sender's domain — the exact defect ``runner.py`` documents. So this
    colleague is EXTERNAL to gate 1 and internal to gate 2, and deleting the
    internal-domain gate mints a lead for him.
    """
    _seed_account(db, org_domains=["ops@fracktalworks.com"])
    _seed_status(db)
    _seed_cursor(db)
    _message(db, address="ishaan@fracktalworks.com", name="Ishaan Pilar")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_internal"] == 1
    assert _leads(db) == []


async def test_dw5_the_mailbox_owner_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db, address=OWNER, name="Vijay")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_internal"] == 1
    assert _leads(db) == []


async def test_dw5_an_already_known_contact_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    db.seed("crm_contacts", first_name="Asha", email=STRANGER)
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_known"] == 1
    assert _leads(db) == []


async def test_dw5_an_existing_lead_with_that_address_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    db.seed("crm_leads", lead_name="Asha", email=STRANGER, source="manual")
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_known"] == 1
    assert len(_leads(db)) == 1  # the seeded one, unchanged


async def test_dw5_a_sender_we_have_emailed_before_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """`_maybe_block_cold`'s second step, mirrored: someone we have written to
    is a correspondent, not a cold inbound inquiry."""
    _ready(db)
    db.seed("email_messages", account_id=ACCOUNT_ID, folder="SENT",
            from_address={"email": OWNER},
            to_addresses=[{"email": STRANGER}],
            rules_processed_at=None, rules_held_back_at=None)
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_known"] == 1
    assert _leads(db) == []


async def test_dw5_mail_sent_to_someone_else_does_not_suppress_the_lead(
    db: FakeCrmDB, on: None,
) -> None:
    """The control for the case above — and the one the shared fake could not
    see before this slice taught it ``@>``. Without a containment reader the
    probe matches every Sent message and no lead is ever created, which reads
    as a passing "no lead" test for entirely the wrong reason."""
    _ready(db)
    db.seed("email_messages", account_id=ACCOUNT_ID, folder="SENT",
            from_address={"email": OWNER},
            to_addresses=[{"email": "somebody.else@elsewhere.com"}],
            rules_processed_at=None, rules_held_back_at=None)
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["created"] == 1


async def test_dw5_a_sender_in_the_cold_memo_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """`_maybe_block_cold`'s first step: this account has already decided
    something about this address — flagged it cold, or whitelisted it. Either
    way it is not a stranger who just wrote in."""
    _ready(db)
    db.seed("email_cold_senders", account_id=ACCOUNT_ID, from_email=STRANGER,
            status="AI_LABELED_COLD")
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_known"] == 1
    assert _leads(db) == []


async def test_dw5_a_message_with_no_sender_address_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """`sender_scope` fails SAFE to "external", which is the wrong direction
    here. An address we could not parse has no domain, so the second gate
    refuses it."""
    _ready(db)
    _message(db, address="", name="Anonymous")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_unusable"] == 1
    assert _leads(db) == []


# ── done-when 6: born dirty, and the activity that never leaves ─────────────

async def test_dw6_each_created_lead_is_born_zoho_dirty(
    db: FakeCrmDB, on: None,
) -> None:
    """D-CRM-9, asserted rather than left implied: an auto-lead enters the
    push queue exactly like a human's, which is precisely why the flip is an
    owner gate. `mark_dirty_on_insert` lives inside `core.insert_row`, so this
    also fails if the write path is swapped for raw SQL."""
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert _leads(db)[0]["zoho_dirty"] is True
    assert _leads(db)[0].get("zoho_id") is None


def test_dw6_the_first_activity_type_is_excluded_from_the_push_predicate(
) -> None:
    """Asserted against ``push_activities``' OWN statement text, not against
    the constant beside it: the predicate is spelled inline in the SQL, and a
    test that only read ``PUSHABLE_ACTIVITY_TYPES`` would keep passing if the
    statement drifted from it."""
    source = inspect.getsource(crm_sync.push_activities)
    match = re.search(r"type\s+IN\s*\(([^)]*)\)", source, re.I)
    assert match, "push_activities no longer filters on an activity type list"
    pushed = {value.strip().strip("'\"") for value in match.group(1).split(",")}
    assert auto_lead.ACTIVITY_TYPE not in pushed, (
        f"the auto-lead activity type {auto_lead.ACTIVITY_TYPE!r} is now in "
        "the Zoho push predicate — the mail's subject and sender would leave "
        "the native CRM for the live tenant"
    )
    assert pushed == set(crm_sync.PUSHABLE_ACTIVITY_TYPES)


async def test_dw6_the_activity_carries_no_zoho_id_and_is_not_pushable(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    activity = _activities(db)[0]
    assert activity["type"] not in crm_sync.PUSHABLE_ACTIVITY_TYPES


# ── done-when 7: a deep resync mints nothing ────────────────────────────────

async def test_dw7_a_deep_resync_of_a_year_old_backlog_mints_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """The failure this whole cursor exists to prevent.

    ``resync_account`` runs a ~1-year all-folder backfill and then fires
    ``process_new_mail``; a first-ever sync of a newly connected mailbox is
    deep by the same heuristic; and neither stamps ``rules_held_back_at``. So
    the backlog below is INDISTINGUISHABLE from new mail on every predicate
    except ``received_at > activated_at``. Delete that one and this seeds 30
    leads into a live Zoho push queue.
    """
    _ready(db)
    for index in range(30):
        _message(
            db, address=f"stranger{index}@elsewhere.com",
            received_at=_at(1, year=2025) + timedelta(days=index),
            # Classified NOW, by the resync — which is the trap: the
            # incremental cursor alone says every one of these is new.
            processed_at=_at(6, 12),
        )

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 0
    assert stats["created"] == 0
    assert _leads(db) == []
    assert _activities(db) == []


async def test_dw7_the_first_on_state_run_activates_and_mints_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """No cursor row yet — which is every mailbox on the day the flag flips.
    ``activated_at`` and ``processed_watermark`` are stamped to the same
    instant, so the activating cycle itself considers nothing."""
    _seed_account(db)
    _seed_status(db)
    _message(db, received_at=_at(5))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    cursors = db.rows("crm_auto_lead_cursors")
    assert len(cursors) == 1
    assert cursors[0]["activated_at"] == cursors[0]["processed_watermark"]
    assert stats["candidates"] == 0
    assert _leads(db) == []


async def test_dw7_activation_writes_the_row_once(
    db: FakeCrmDB, on: None,
) -> None:
    _seed_account(db)
    _seed_status(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    stamped = db.rows("crm_auto_lead_cursors")[0]["activated_at"]
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert len(db.rows("crm_auto_lead_cursors")) == 1
    assert db.rows("crm_auto_lead_cursors")[0]["activated_at"] == stamped


async def test_dw7_mail_that_arrived_after_activation_still_mints(
    db: FakeCrmDB, on: None,
) -> None:
    """The control: the discriminator must not be "mint nothing, ever"."""
    _ready(db)
    _message(db, received_at=_at(2), processed_at=_at(2))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["created"] == 1


# ── The per-cycle cap ───────────────────────────────────────────────────────

async def test_the_cycle_is_capped_and_the_overflow_is_counted(
    db: FakeCrmDB, on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent truncation reads as "covered everything", so the remainder is a
    number in the log line."""
    monkeypatch.setattr(auto_lead, "MAX_CANDIDATES_PER_CYCLE", 3)
    _ready(db)
    for index in range(7):
        _message(db, address=f"stranger{index}@elsewhere.com",
                 received_at=_at(5) + timedelta(minutes=index),
                 processed_at=_at(5) + timedelta(minutes=index))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 3
    assert stats["overflow"] == 4
    assert stats["created"] == 3


async def test_the_cap_leaves_the_remainder_for_the_next_cycle(
    db: FakeCrmDB, on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watermark advances over what was CONSIDERED, so the overflow is
    deferred rather than dropped. Ordering by ``rules_processed_at`` is what
    makes that true — ordering by ``received_at`` would skip the remainder."""
    monkeypatch.setattr(auto_lead, "MAX_CANDIDATES_PER_CYCLE", 3)
    _ready(db)
    for index in range(5):
        _message(db, address=f"stranger{index}@elsewhere.com",
                 received_at=_at(5) + timedelta(minutes=index),
                 processed_at=_at(5) + timedelta(minutes=index))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    second = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert second["candidates"] == 2
    assert len(_leads(db)) == 5


async def test_a_quiet_cycle_costs_no_count_query(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db, received_at=_at(5))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert db.statements_touching("SELECT COUNT(*) FROM email_messages") == []


# ── Robustness ──────────────────────────────────────────────────────────────

async def test_an_account_with_no_recorded_owner_is_skipped(
    db: FakeCrmDB, on: None,
) -> None:
    """Attributing a lead to 'anonymous' is worse than not creating it: the
    `owner` filter has nothing to match and it is nobody's follow-up."""
    _seed_account(db, owner="")
    _seed_status(db)
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 0
    assert _leads(db) == []
    # It returns before the cursor is even activated: an account it will never
    # write for has nothing to remember.
    assert db.rows("crm_auto_lead_cursors") == []


async def test_a_missing_account_row_is_skipped(
    db: FakeCrmDB, on: None,
) -> None:
    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 0
    assert db.rows("crm_auto_lead_cursors") == []


async def test_a_failed_activity_write_does_not_lose_the_batch(
    db: FakeCrmDB, on: None,
) -> None:
    """The WS-26b lesson: Postgres aborts the TRANSACTION on a statement
    error, so a bare per-record try/except loses every row after the bad one.
    The savepoint is what makes "one bad activity" true."""
    _ready(db)
    _message(db, address="first@elsewhere.com", processed_at=_at(5, 9))
    _message(db, address="second@elsewhere.com", processed_at=_at(5, 10))
    db.fail_on("INSERT INTO crm_activities", times=1)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    # Both leads exist, and BOTH are counted created — see the test below for
    # why that is the point rather than a rounding error.
    assert stats["created"] == 2
    assert stats["activity_errors"] == 1
    assert stats["errors"] == 0
    assert len(_leads(db)) == 2
    assert len(_activities(db)) == 1
    assert db.savepoints == 2
    assert db.savepoint_rollbacks == 1


async def test_a_lead_whose_activity_failed_is_still_counted_created(
    db: FakeCrmDB, on: None, log: _Log,
) -> None:
    """`created` counts COMMITTED LEADS, not completed pairs.

    The lead is committed on its own session before the activity is attempted,
    so by the time the activity fails the row exists and is already
    `zoho_dirty` — it WILL push to the live tenant. A cycle that logged
    `created=0` next to that row would send whoever read it looking in the
    wrong place entirely.
    """
    _ready(db)
    _message(db)
    db.fail_on("INSERT INTO crm_activities", times=1)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["created"] == 1
    assert stats["activity_errors"] == 1
    assert len(_leads(db)) == 1
    assert _leads(db)[0]["zoho_dirty"] is True
    failures = log.at("warning", "crm.auto_lead_activity_failed")
    assert len(failures) == 1
    assert failures[0]["lead_id"] == _leads(db)[0]["id"]


async def test_the_activity_failure_does_not_hold_the_cursor(
    db: FakeCrmDB, on: None,
) -> None:
    """It must not: the lead is committed, so the next cycle finds it at step 3
    and skips — the activity would never be retried and the cursor would stall
    forever on work that cannot be redone."""
    _ready(db)
    _message(db, processed_at=_at(5, 9))
    db.fail_on("INSERT INTO crm_activities", times=1)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == _at(5, 9)


async def test_the_watermark_advances_over_messages_that_minted_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """Otherwise a colleague's message is re-read, re-probed and re-rejected
    on every sync cycle for the life of the mailbox."""
    _ready(db)
    _message(db, address="cfo@fracktal.in", processed_at=_at(5, 9))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == _at(5, 9)


async def test_the_cursor_is_read_and_written_per_account(
    db: FakeCrmDB, on: None,
) -> None:
    """One mailbox's activation must not silence another's: the cursor is
    keyed on ``account_id`` and every statement binds it."""
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    for statement in db.statements_touching("crm_auto_lead_cursors"):
        assert ":account_id" in statement


def test_the_module_registers_no_routes() -> None:
    """Like ``broker_handlers``: it serves no HTTP surface, so it is not
    imported from ``routes/crm/__init__.py`` and defines no route."""
    source = inspect.getsource(auto_lead)
    assert "@router." not in source
    package = (
        REPO / "apps/services/gateway/gateway/routes/crm/__init__.py"
    ).read_text(encoding="utf-8")
    assert "auto_lead" not in package


# ── done-when 8: an OFF→ON round trip mints nothing from the OFF window ─────

async def test_dw8_the_first_message_after_an_overnight_lull_is_minted(
    db: FakeCrmDB, on: None, log: _Log, clock: _Clock,
) -> None:
    """The regression the CLAMP exists to prevent, in its real shape.

    ⚠️ This step is NOT invoked once per scheduler period.
    ``email_ingestion/scheduler.py:463-472`` reads ``synced`` off the sync
    result and fires the hook **only when mail was actually persisted**, so a
    mailbox with no new mail does not run it at all. Which means the cycle
    that trips the dormancy test is ALWAYS the cycle carrying the message that
    woke it — and a re-anchor that stamped ``now`` and returned early excluded
    exactly that message, permanently, every night and every weekend.

    So the lull below is expressed the way the scheduler produces it: by NOT
    CALLING the step. Nothing about ``last_run_at`` is hand-seeded.
    """
    _seed_account(db)
    _seed_status(db)

    # 22:00 — a sync persisted mail, the step runs, the cursor is activated.
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    # 22:05 — an ordinary evening cycle, minting normally.
    clock.advance(minutes=5)
    _message(db, address="evening@elsewhere.com",
             received_at=clock() - timedelta(minutes=2),
             processed_at=clock() - timedelta(minutes=1))
    evening = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    assert evening["created"] == 1
    assert evening["reanchored"] == 0

    # ── THE LULL ── nine and a half hours in which no mail arrives, so the
    # scheduler never fires the hook and this step is never entered.
    clock.advance(hours=9, minutes=26)

    # 07:31 — a cold prospect wrote at 07:30:45 and the sync persisted it.
    _message(db, address="prospect@elsewhere.com",
             received_at=clock() - timedelta(seconds=15),
             processed_at=clock() - timedelta(seconds=5))
    morning = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert morning["reanchored"] == 1, "the lull must trip the dormancy test"
    assert morning["created"] == 1, (
        "the message that WOKE the step was excluded by the anchor the same "
        "cycle stamped — the overnight regression is back"
    )
    assert sorted(row["email"] for row in _leads(db)) == [
        "evening@elsewhere.com", "prospect@elsewhere.com",
    ]
    reanchors = log.at("warning", "sync.auto_lead_reanchored")
    assert len(reanchors) == 1
    assert reanchors[0]["gap_seconds"] > auto_lead.REANCHOR_GAP_SECONDS


async def test_dw8_the_clamp_lands_one_gap_width_back_not_at_now(
    db: FakeCrmDB, on: None, clock: _Clock,
) -> None:
    """The mechanism, asserted directly: the anchor moves FORWARD to the start
    of the gap window, never to the clock. It is what admits the triggering
    message while still excluding the window behind it."""
    _seed_account(db)
    _seed_status(db)
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    clock.advance(days=27)
    _message(db, received_at=clock() - timedelta(seconds=10),
             processed_at=clock() - timedelta(seconds=5))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    anchored = db.rows("crm_auto_lead_cursors")[0]["activated_at"]
    assert anchored == clock() - timedelta(
        seconds=auto_lead.REANCHOR_GAP_SECONDS)
    assert anchored < clock()


async def test_dw8_an_off_then_on_round_trip_mints_nothing_from_the_gap(
    db: FakeCrmDB, on: None, log: _Log, clock: _Clock,
) -> None:
    """The hole ``activated_at`` alone does NOT cover, and the case the clamp
    must not regress.

    Flag on day 1, off for 27 days, on again. Nothing about the cursor changed
    while the flag was off, so a naive anchor still says day 1 and every
    message in the OFF window passes both predicates — the first ON cycle
    mints the entire four-week backlog in one batch, each lead pushing
    unattended into the live tenant (27 of 27, measured).
    """
    _seed_account(db)
    _seed_status(db)
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    for index in range(27):
        clock.advance(days=1)
        _message(db, address=f"stranger{index}@elsewhere.com",
                 received_at=clock(), processed_at=clock())
    # The flag comes back on a full day after the last of it arrived, so the
    # whole window sits outside the clamp's one-hour tail.
    clock.advance(days=1)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["reanchored"] == 1
    assert stats["candidates"] == 0
    assert stats["created"] == 0
    assert _leads(db) == []
    assert _activities(db) == []
    assert len(log.at("warning", "sync.auto_lead_reanchored")) == 1


async def test_dw8_mail_from_the_last_hour_of_the_off_window_is_minted(
    db: FakeCrmDB, on: None, clock: _Clock,
) -> None:
    """The DOCUMENTED RESIDUAL, asserted deliberately rather than accidentally.

    Clamping means the tail of the gap window is admitted: mail received
    within ``REANCHOR_GAP_SECONDS`` of the flag coming back on IS minted. The
    bound is on TIME, not on volume — one gap-width of mail (an hour), drained
    across however many cycles the per-cycle cap takes, since the cap defers
    rather than drops. It is the price of never dropping the message that woke
    the step, which is the same message on every ordinary night. Recorded in
    `crm_app.md` §9 and in the owner note on `work_plan.md` §6 (b).
    """
    _seed_account(db)
    _seed_status(db)
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    clock.advance(days=27)

    _message(db, address="mid_window@elsewhere.com",
             received_at=clock() - timedelta(days=3),
             processed_at=clock() - timedelta(days=3))
    _message(db, address="half_an_hour_ago@elsewhere.com",
             received_at=clock() - timedelta(minutes=30),
             processed_at=clock() - timedelta(minutes=30))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["reanchored"] == 1
    assert [row["email"] for row in _leads(db)] == [
        "half_an_hour_ago@elsewhere.com"
    ], "the accepted bound is ONE gap width — no more, and no less"


async def test_dw8_every_cycle_stamps_last_run_at(
    db: FakeCrmDB, on: None,
) -> None:
    """Including the ones that considered nothing — otherwise a mailbox with
    no new mail drifts into looking dormant purely by being calm."""
    _seed_account(db)
    _seed_status(db)
    stale = datetime.now(UTC) - timedelta(seconds=300)
    _seed_cursor(db, last_run_at=stale)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert db.rows("crm_auto_lead_cursors")[0]["last_run_at"] > stale


# ── done-when 9: a failure never advances the cursor past lost work ─────────

async def test_dw9_the_watermark_advances_over_the_successful_prefix_only(
    db: FakeCrmDB, on: None, log: _Log,
) -> None:
    """[ok, raise, ok] — the shape a pool exhaustion produces.

    This step opens a SECOND session per lead (``create_record``) while
    holding the batch's own, so a pool that runs out fails several candidates
    at once. An unconditional advance would step the cursor over every one of
    them permanently: the reviewer measured 3 candidates, 3 errors, watermark
    advanced, three leads lost for good.
    """
    _ready(db)
    _message(db, address="first@elsewhere.com", processed_at=_at(5, 9))
    _message(db, address="second@elsewhere.com", processed_at=_at(5, 10))
    _message(db, address="third@elsewhere.com", processed_at=_at(5, 11))
    # `after=1` so the poison lands on the SECOND candidate: a prefix rule and
    # an unconditional advance are indistinguishable when the FIRST record is
    # the one that fails.
    db.fail_on("INSERT INTO crm_leads", times=1, after=1)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["candidates"] == 3
    assert stats["errors"] == 1
    # Held at message ONE's stamp — not message three's.
    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == _at(5, 9)


async def test_dw9_the_next_cycle_reconsiders_everything_after_the_failure(
    db: FakeCrmDB, on: None,
) -> None:
    """The other half: holding the cursor is only useful if the work comes
    back. Message 3 succeeded in cycle 1 and is re-considered in cycle 2 —
    that is free, because step 3 of the unknown-sender check finds the lead it
    already created and skips."""
    _ready(db)
    _message(db, address="first@elsewhere.com", processed_at=_at(5, 9))
    _message(db, address="second@elsewhere.com", processed_at=_at(5, 10))
    _message(db, address="third@elsewhere.com", processed_at=_at(5, 11))
    db.fail_on("INSERT INTO crm_leads", times=1, after=1)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    second = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert second["candidates"] == 2          # messages 2 and 3, again
    assert second["created"] == 1             # message 2, finally
    assert second["skipped_known"] == 1       # message 3, already a lead
    assert sorted(row["email"] for row in _leads(db)) == [
        "first@elsewhere.com", "second@elsewhere.com", "third@elsewhere.com",
    ]


async def test_dw9_a_stuck_head_message_is_logged_at_warning_every_cycle(
    db: FakeCrmDB, on: None, log: _Log,
) -> None:
    """A held cursor and a quiet mailbox both create nothing, so the counters
    cannot tell them apart. The WARNING is the whole difference, and it
    repeats — an INFO line saying `created=0` is what a stuck head message
    looked like before."""
    _ready(db)
    _message(db, address="poison@elsewhere.com", processed_at=_at(5, 9))
    db.fail_on("INSERT INTO crm_leads", times=2)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    stalls = log.at("warning", "sync.auto_lead_stalled")
    assert len(stalls) == 2, (
        "the stall must be reported on EVERY cycle it persists, not once"
    )
    assert stalls[0]["errors"] == 1
    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == ACTIVATED


async def test_dw9_a_healthy_cycle_logs_no_stall(
    db: FakeCrmDB, on: None, log: _Log,
) -> None:
    _ready(db)
    _message(db)

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert log.at("warning", "sync.auto_lead_stalled") == []


# ── The Sent probe folds case (P2-4) ───────────────────────────────────────

async def test_a_reply_from_someone_we_emailed_in_another_case_mints_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """The owner wrote to ``Asha@AcmeRobotics.com``; she replies from
    ``asha@acmerobotics.com``. Postgres's ``@>`` is case-EXACT, so the
    containment form of this probe says we have never emailed her and mints a
    lead for somebody already mid-conversation. This module folds case
    explicitly; ``_maybe_block_cold`` is left alone, being the email package's
    predicate with its own blast radius."""
    _ready(db)
    db.seed("email_messages", account_id=ACCOUNT_ID, folder="SENT",
            from_address={"email": OWNER},
            to_addresses=[{"email": "Asha@AcmeRobotics.com"}],
            rules_processed_at=None, rules_held_back_at=None)
    _message(db, address="asha@acmerobotics.com")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_known"] == 1
    assert _leads(db) == []


async def test_the_case_folding_probe_still_distinguishes_recipients(
    db: FakeCrmDB, on: None,
) -> None:
    """Folding case must not fold everything: mail to somebody else is still
    mail to somebody else."""
    _ready(db)
    db.seed("email_messages", account_id=ACCOUNT_ID, folder="SENT",
            from_address={"email": OWNER},
            to_addresses=[{"email": "Someone.Else@Elsewhere.com"}],
            rules_processed_at=None, rules_held_back_at=None)
    _message(db)

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["created"] == 1


# ── Subdomain colleagues (P2-6) ────────────────────────────────────────────

async def test_a_colleague_on_a_subdomain_creates_nothing(
    db: FakeCrmDB, on: None,
) -> None:
    """``cfo@mail.fracktal.in`` is the CFO. Exact matching alone let him
    through while ``cfo@fracktal.in`` was caught, and a company's own
    ``mail.``/``corp.``/regional subdomains are routine."""
    _ready(db)
    _message(db, address="cfo@mail.fracktal.in", name="Our CFO")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["skipped_internal"] == 1
    assert _leads(db) == []


async def test_a_lookalike_domain_is_not_a_subdomain(
    db: FakeCrmDB, on: None,
) -> None:
    """The mistake a bare ``endswith`` makes: ``notfracktal.in`` is a
    different company, and refusing its leads would be the suffix test doing
    real damage in the other direction."""
    _ready(db)
    _message(db, address="sales@notfracktal.in", name="Not Us")

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["created"] == 1


# ── The cap's tie boundary (P2-5) ──────────────────────────────────────────

def test_the_candidate_order_carries_a_tiebreak() -> None:
    """Asserted as SQL TEXT, deliberately: the shared fake orders on the first
    key only, so the tiebreak is not observable through it. Without ``, id``
    the order of rows sharing a timestamp is undefined and the cap could cut a
    tie group in a different place on each read."""
    assert "ORDER BY rules_processed_at, id" in auto_lead._CANDIDATE_SQL


async def test_a_tie_group_split_by_the_cap_is_deferred_whole(
    db: FakeCrmDB, on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watermark is a TIMESTAMP, so advancing it to a stamp the cap cut
    through would step over the rest of that group forever."""
    monkeypatch.setattr(auto_lead, "MAX_CANDIDATES_PER_CYCLE", 3)
    _ready(db)
    for index, stamp in enumerate(
        [_at(5, 9), _at(5, 10), _at(5, 11), _at(5, 11), _at(5, 12)]
    ):
        _message(db, address=f"stranger{index}@elsewhere.com",
                 received_at=_at(5), processed_at=stamp)

    first = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert first["boundary_deferred"] == 1
    assert first["candidates"] == 2
    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == _at(5, 10)

    second = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert second["candidates"] == 3          # the whole tie group, plus 5
    assert len(_leads(db)) == 5


async def test_a_cap_that_falls_between_groups_defers_nothing(
    db: FakeCrmDB, on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control, and the reason the drop is conditional: deferring the last
    group unconditionally would shrink every capped batch by one message for
    no reason at all."""
    monkeypatch.setattr(auto_lead, "MAX_CANDIDATES_PER_CYCLE", 3)
    _ready(db)
    for index in range(5):
        _message(db, address=f"stranger{index}@elsewhere.com",
                 received_at=_at(5),
                 processed_at=_at(5, 9) + timedelta(minutes=index))

    stats = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert stats["boundary_deferred"] == 0
    assert stats["candidates"] == 3


# ── Attacker-controlled text is bounded ────────────────────────────────────

async def test_a_huge_display_name_and_subject_are_clipped(
    db: FakeCrmDB, on: None,
) -> None:
    """Nothing upstream bounds either: a display name is whatever the sending
    server put in the header, and it lands in a column every CRM list, board
    card and Zoho push then carries."""
    _ready(db)
    _message(db, name="Asha " + ("x" * 5000), subject="Q: " + ("y" * 5000))

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    lead = _leads(db)[0]
    assert len(lead["last_name"]) <= auto_lead.MAX_NAME_CHARS
    assert len(lead["lead_name"]) <= auto_lead.MAX_NAME_CHARS + len("Asha ")
    assert lead["last_name"].endswith(auto_lead.CLIP_MARKER)
    activity = _activities(db)[0]
    assert len(activity["subject"]) == auto_lead.MAX_SUBJECT_CHARS
    assert activity["subject"].endswith(auto_lead.CLIP_MARKER)


async def test_an_ordinary_name_and_subject_are_untouched(
    db: FakeCrmDB, on: None,
) -> None:
    _ready(db)
    _message(db, name="Asha Menon", subject="Quote for 40 printers")

    await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert _leads(db)[0]["lead_name"] == "Asha Menon"
    assert auto_lead.CLIP_MARKER not in _activities(db)[0]["subject"]


# ── The migration, read as text ─────────────────────────────────────────────

def _cursor_migration() -> Path:
    """Found by CONTENT, never by number — R1 forbids writing an absolute
    future migration number anywhere, and a test pinned to a number is the
    same mistake in a different file."""
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_auto_lead_cursors" in path.read_text(
            encoding="utf-8",
        )
    ]
    assert len(found) == 1, (
        f"expected exactly one migration creating crm_auto_lead_cursors, "
        f"found {[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def migration() -> str:
    return _cursor_migration().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bare(migration: str) -> str:
    """Statements only — a check its own explanatory comment can satisfy is a
    check that fails open."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in migration.splitlines())


def test_the_migration_number_is_unique() -> None:
    """The property that actually protects the ladder, and the ONLY one this
    branch can hold.

    Two migrations sharing a number replay in filename order against the wrong
    schema — that is the failure, and it is asserted. Contiguity is
    deliberately NOT asserted: open PR #399 holds 157, so this file took 158
    and the ladder carries a reservation gap until that PR lands. (Nor is "the
    highest number in the repo" asserted — `test_crm_migration.py` records
    that version going red the moment any later workstream's migration landed,
    and a red unit test silently blocks deploy.)
    """
    numbers = [
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    ]
    mine = int(_cursor_migration().name.split("_", 1)[0])
    assert numbers.count(mine) == 1, (
        f"two migrations share number {mine} — the ladder replays them in "
        "filename order, so one runs against the wrong schema"
    )


def test_the_migration_number_records_why_it_skipped_one() -> None:
    """A gap in the ladder is a thing a future reader will trip over, so the
    file says who holds the number it skipped."""
    header = _cursor_migration().read_text(encoding="utf-8")[:1200]
    assert "#399" in header, (
        "the migration skips a number without naming the PR that holds it"
    )


def test_the_migration_header_says_what_why_and_what_it_depends_on() -> None:
    lines = _cursor_migration().read_text(encoding="utf-8").splitlines()
    header = "\n".join(
        line for line in lines[: next(
            index for index, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("--")
        )]
    )
    for required in ("What:", "Why:", "Depends on:"):
        assert required in header, f"the migration header has no '{required}'"


def test_the_migration_is_idempotent(bare: str) -> None:
    unguarded = [
        match.group(0)
        for match in re.finditer(r"CREATE\s+(TABLE|INDEX)\s+(\S+)", bare, re.I)
        if not match.group(2).upper().startswith("IF")
    ]
    assert not unguarded, (
        f"unguarded CREATE: {unguarded}. apply_migrations.sh replays the whole "
        "ladder on every deploy"
    )


def test_the_column_added_after_the_first_cut_is_repaired_in_place(
    bare: str,
) -> None:
    """`CREATE TABLE IF NOT EXISTS` is a no-op against a database that already
    has the table, so it cannot ADD a column to one. A scratch database that
    applied this file while it was still the two-column version would keep the
    old shape and fail on every cycle."""
    assert re.search(
        r"ALTER TABLE crm_auto_lead_cursors\s+ADD COLUMN IF NOT EXISTS\s+last_run_at",
        bare,
    ), "no guarded ADD COLUMN for last_run_at — an already-created table keeps the old shape"
    assert re.search(
        r"ALTER COLUMN last_run_at SET NOT NULL", bare,
    ), "the repaired column must end up NOT NULL like the created one"
    assert re.search(
        r"UPDATE crm_auto_lead_cursors\s+SET last_run_at = COALESCE", bare,
    ), "SET NOT NULL will fail unless existing rows are backfilled first"


def test_the_migration_drops_or_truncates_nothing(bare: str) -> None:
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE\s+FROM)\b", bare, re.I)


def test_the_cursor_carries_all_three_timestamps_not_null(bare: str) -> None:
    """Three columns because that is three questions: which ON epoch is this
    (`activated_at`), how far has the step got (`processed_watermark`), and did
    it run at all (`last_run_at`). A NULL on any of them is a predicate that
    matches nothing, which reads exactly like a working feature."""
    for column in ("activated_at", "processed_watermark", "last_run_at"):
        assert re.search(rf"{column}\s+TIMESTAMPTZ\s+NOT NULL", bare), (
            f"{column} must exist and be NOT NULL"
        )


def test_the_cursor_is_keyed_and_cascaded_on_the_account(bare: str) -> None:
    assert re.search(
        r"account_id\s+UUID\s+PRIMARY KEY\s+REFERENCES\s+email_accounts\s*\(\s*id\s*\)"
        r"\s+ON DELETE CASCADE",
        bare,
    ), "the cursor must be one row per account, cascading with the mailbox"


def test_the_migration_adds_no_unique_index_on_a_lead_address(
    bare: str,
) -> None:
    """The recorded refusal (spec §9): the cross-invocation double-mint race is
    ACCEPTED. A UNIQUE index on `crm_leads.email` — where 1,516 imported rows
    may already carry duplicates — is a deploy-blocking constraint of exactly
    the shape migration 148 had to defuse."""
    assert not re.search(r"UNIQUE.*crm_leads", bare, re.I | re.S)
    assert "crm_leads" not in bare


# ── Mirror mode (WS-53 CRM-Z6) ──────────────────────────────────────────────

async def test_mirror_mode_makes_no_lead_and_advances_the_cursor(
    db: FakeCrmDB, on: None,
) -> None:
    """Zoho owns new leads in Mirror mode. The step must not count an error, and
    the watermark must move, or every cycle re-reads the same batch."""
    _ready(db)
    db.seed("crm_settings", organization_id=MAILBOX_ORG, mode="mirror")
    _message(db, processed_at=_at(5))

    first = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)
    second = await auto_lead.create_leads_from_new_mail(ACCOUNT_ID)

    assert _leads(db) == []
    assert first["errors"] == 0 and first["created"] == 0
    assert first["candidates"] == 1
    assert second["candidates"] == 0
    assert db.rows("crm_auto_lead_cursors")[0]["processed_watermark"] == _at(5)
