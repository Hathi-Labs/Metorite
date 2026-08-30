"""WS-31 CP-10 slice 2 — tasks, units and capabilities.

Spec: ``project-docs/specs/customer_console.md`` §6A.9 order steps 1 and 2 ·
D60 · D61 (G-3, G-4, G-5) · D19.2.

🔴 **The hole this closes was live.** ``credits.rate_call`` was tokens-only, so
``tier-stt`` — which ships in the production seed — could not be priced, and
neither could ``speak`` or ``image``. Three of six tasks.

**R8.** Every clause here runs against a real Postgres 16 through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console.credits import UnpricedModel
from customer_console.router import TierUnknown, resolve_invocation, resolve_rate_card, resolve_tier
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
LADDER = ROOT / "infra/customer_console"

WHISPER = "groq/whisper-large-v3-turbo"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def conn():
    """A connection whose writes are ROLLED BACK.

    ⚠️ This suite inserts priced and absorbed rate cards to prove the
    mechanism. Committing them would break a SIBLING fence —
    `test_customer_console_sql.py::test_the_rate_card_ships_unpriced`,
    whose claim is about what the SEED ships. Measured: it went red the
    first time this suite ran ahead of it on a shared database.
    """
    eng = create_engine(_URL, future=True)
    with eng.connect() as c:
        trans = c.begin()
        try:
            yield c
        finally:
            trans.rollback()


# ── The task allowlist (G-5) ────────────────────────────────────────────────

class TestTheTaskAllowlist:
    def test_the_eight_tasks_are_seeded(self, conn):
        # video and music joined in 015 (D67's slate), priced in seconds.
        slugs = {r[0] for r in conn.execute(text("SELECT slug FROM task_catalog"))}
        assert slugs == {"chat", "embed", "vision", "transcribe", "speak",
                         "image", "video", "music"}

    def test_each_task_records_the_unit_it_is_priced_in(self, conn):
        """An operator must not be able to price `transcribe` per 1k tokens."""
        units = dict(conn.execute(
            text("SELECT slug, natural_unit FROM task_catalog")).all())
        assert units["chat"] == "tokens"
        assert units["transcribe"] == "minutes"
        assert units["speak"] == "characters"
        assert units["image"] == "images"

    def test_an_unknown_task_is_refused_by_the_database(self, conn):
        """Tasks are an allowlist, tiers are free text — G-5, asymmetric.

        A typo that silently created a task would route an image request to a
        chat model, which is D32.7's coercion defect in different clothes.
        """
        with pytest.raises(Exception) as exc:
            conn.execute(text(
                "INSERT INTO tier_binding (tier, model, task) "
                "VALUES ('tier-x', 'm', 'trasncribe')"))
        assert "foreign key" in str(exc.value).lower()

    def test_a_tier_name_is_NOT_constrained(self, conn):
        # The other half of the asymmetry: a tier is a name we sell.
        conn.execute(text(
            "INSERT INTO tier_binding (tier, model, task) "
            "VALUES (:t, 'm', 'chat')"), {"t": f"tier-{uuid.uuid4().hex[:6]}"})


# ── Two-step resolution (D60) ───────────────────────────────────────────────

class TestResolution:
    def test_the_stt_tier_is_bound_to_transcribe_not_chat(self, conn):
        """🔴 The defect the migration had to fix, as a permanent fence.

        `002_seed_catalog.sql` runs BEFORE the `task` column exists, so a
        `tier-stt` row seeded there is necessarily tagged `chat` — which would
        hand audio to `acompletion`. 010 owns this binding for that reason.
        """
        rows = conn.execute(text(
            "SELECT task FROM tier_binding WHERE tier = 'tier-stt'")).all()
        assert [r[0] for r in rows] == ["transcribe"]

    def test_resolving_a_bound_task_returns_its_model(self, conn):
        resolved = resolve_tier(conn, "tier-stt", "transcribe")
        assert resolved.model == WHISPER
        assert resolved.task == "transcribe"

    def test_an_unbound_task_RAISES_rather_than_coercing_to_chat(self, conn):
        """§6A.9 rule 2. An unbound task is a 400, never a coercion.

        Falling back to the chat binding answers an image request with a
        paragraph, and bills for it.
        """
        with pytest.raises(TierUnknown):
            resolve_tier(conn, "tier-stt", "image")

    def test_chat_stays_the_default_so_every_existing_caller_is_unchanged(
            self, conn):
        assert resolve_tier(conn, "tier-fast").task == "chat"
        assert resolve_tier(conn, "tier-fast").model

    def test_the_invocation_comes_from_data_not_a_frozenset(self, conn):
        """Replaces `_STT_TIER_IDS`, which could not grow a row (D60.2)."""
        assert resolve_invocation(conn, WHISPER, "transcribe") == "atranscription"

    def test_a_chat_model_declares_acompletion_and_streams(self, conn):
        model = resolve_tier(conn, "tier-fast").model
        assert resolve_invocation(conn, model, "chat") == "acompletion"
        streams = conn.execute(text(
            "SELECT streams FROM model_capability "
            "WHERE model = :m AND task = 'chat'"), {"m": model}).scalar_one()
        assert streams is True

    def test_streaming_is_per_model_and_task(self, conn):
        """§6A.9 rule 4. Only chat and speak stream, and not on every model."""
        streams = conn.execute(text(
            "SELECT streams FROM model_capability "
            "WHERE model = :m AND task = 'transcribe'"),
            {"m": WHISPER}).scalar_one()
        assert streams is False

    def test_an_undeclared_capability_raises(self, conn):
        """Guessing `acompletion` is how audio reaches a chat endpoint."""
        with pytest.raises(TierUnknown):
            resolve_invocation(conn, WHISPER, "image")


# ── Pricing modes and units ─────────────────────────────────────────────────

class TestPricing:
    def test_the_seed_still_ships_unpriced(self, conn):
        """⚠️ This slice builds the mechanism to price and prices NOTHING.

        `test_the_rate_card_ships_unpriced` binds unchanged.
        """
        # ⚠️ Scoped to the SEEDED models. Sibling tests in this file insert
        # priced and absorbed rows into the same database, so an
        # unscoped DISTINCT would fail for a reason that has nothing to do
        # with what ships.
        modes = {r[0] for r in conn.execute(text(
            "SELECT DISTINCT pricing_mode FROM model_rate_card "
            "WHERE model LIKE 'deepseek/%' OR model LIKE 'groq/%'"))}
        assert modes == {"unpriced"}

    def test_the_rate_card_is_keyed_on_model_AND_task(self, conn):
        """One model can serve several tasks, at different rates and units."""
        cols = [r[0] for r in conn.execute(text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            " AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'model_rate_card'::regclass AND i.indisprimary"
        ))]
        assert set(cols) == {"model", "task", "effective_from"}

    def test_a_per_minute_card_resolves_with_its_unit(self, conn):
        model = f"test/{uuid.uuid4().hex[:8]}"
        conn.execute(text(
            "INSERT INTO model_rate_card (model, task, unit, "
            " input_credits_per_1k, output_credits_per_1k, "
            " credits_per_unit, pricing_mode) "
            "VALUES (:m, 'transcribe', 'minutes', 0, 0, 0.4, 'priced')"),
            {"m": model})

        card = resolve_rate_card(conn, model, "transcribe")

        assert card.unit == "minutes"
        assert card.credits_per_unit == Decimal("0.400000")
        assert card.is_priced is True

    def test_an_absorbed_task_is_free_and_not_an_error(self, conn):
        """D19.2 absorbs embeddings into the seat price. Not a mistake."""
        model = f"test/{uuid.uuid4().hex[:8]}"
        conn.execute(text(
            "INSERT INTO model_rate_card (model, task, "
            " input_credits_per_1k, output_credits_per_1k, pricing_mode) "
            "VALUES (:m, 'embed', 0, 0, 'absorbed')"), {"m": model})

        card = resolve_rate_card(conn, model, "embed")

        assert card.is_absorbed is True
        assert card.is_priced is False

    def test_an_unknown_pricing_mode_is_refused_by_the_database(self, conn):
        with pytest.raises(Exception) as exc:
            conn.execute(text(
                "INSERT INTO model_rate_card (model, input_credits_per_1k, "
                " output_credits_per_1k, pricing_mode) "
                "VALUES ('m', 0, 0, 'freeish')"))
        assert "pricing_mode" in str(exc.value).lower()

    def test_a_card_for_one_task_does_not_price_another(self, conn):
        model = f"test/{uuid.uuid4().hex[:8]}"
        conn.execute(text(
            "INSERT INTO model_rate_card (model, task, "
            " input_credits_per_1k, output_credits_per_1k, pricing_mode) "
            "VALUES (:m, 'chat', 2, 6, 'priced')"), {"m": model})

        with pytest.raises(UnpricedModel):
            resolve_rate_card(conn, model, "image")


# ── R6 ──────────────────────────────────────────────────────────────────────

class TestTheMigrationIsR6Safe:
    def test_usage_events_new_columns_are_nullable(self, conn):
        """§6A.9 in terms. A back-filled guess must not look measured."""
        nullable = dict(conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'usage_event' "
            "  AND column_name IN ('task', 'quantity', 'unit')")).all())
        assert nullable == {"task": "YES", "quantity": "YES", "unit": "YES"}

    def test_the_seed_uses_a_TARGETLESS_on_conflict(self):
        """⚠️ The trap that broke the first replay run, as a fence.

        `apply_customer_console_migrations.sh` runs EVERY file, EVERY deploy,
        with `ON_ERROR_STOP=1`. So 002 is live code, not history. Migration 010
        widens two primary keys, and a NAMED conflict target that no longer
        matches a constraint is a HARD ERROR.

        Naming the new key instead would break the FIRST deploy carrying 010,
        because 002 executes before 010 in the same pass.
        """
        seed = (LADDER / "002_seed_catalog.sql").read_text(encoding="utf-8")
        for widened in ("(tier, effective_from)", "(model, effective_from)"):
            assert f"ON CONFLICT {widened}" not in seed, (
                f"002 names {widened}, which 010 no longer provides"
            )

    def test_the_seed_does_not_write_a_tier_stt_binding(self):
        """It cannot name a task — it runs before the column exists.

        A `tier-stt` row seeded there is tagged `chat`, and the ladder replays
        every deploy, so it would re-create that wrong row after 010 corrected
        it. Once per deploy, for ever.
        """
        seed = (LADDER / "002_seed_catalog.sql").read_text(encoding="utf-8")
        binding_block = seed[seed.index("INSERT INTO tier_binding"):]
        binding_block = binding_block[:binding_block.index(";")]
        assert "tier-stt" not in binding_block


def test_this_suite_is_named_in_the_ci_skip_guard():
    """The hand-maintained list defends itself, the way the others do.

    ⚠️ A new R8-gated Console suite that is NOT named there still runs — and
    still SKIPS silently when the database variable is missing, reporting
    green. That is the CP-3 failure this guard exists to catch, and the guard
    is a hand-list that nothing discovers.
    """
    ci = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_tasks.py" in ci
