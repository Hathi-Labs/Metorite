"""WS-31 CP-10 slice 2 — tasks, units and capabilities.

Spec: ``project-docs/specs/customer_console.md`` §6A.9 order steps 1 and 2 ·
D60 · D61 (G-3, G-4, G-5) · D19.2. The transcribe ENDPOINT is §6A.10a (H-46).

🔴 **The hole this closes was live.** ``credits.rate_call`` was tokens-only, so
``tier-stt`` — which ships in the production seed — could not be priced, and
neither could ``speak`` or ``image``. Three of six tasks.

🔴 **H-46 closed the other half.** ``tier-stt`` was bound and priceable, and
no route could call it. The last section of this file DRIVES
``POST /v1/audio/transcriptions`` and then reads the row that call wrote.

**R8.** Every clause here runs against a real Postgres 16 through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import catalog
from customer_console import router as router_mod
from customer_console.credits import UnpricedModel
from customer_console.router import TierUnknown, resolve_invocation, resolve_rate_card, resolve_tier
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

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


# ── The transcribe endpoint (H-46, §6A.10a) ─────────────────────────────────
#
# 🔴 **These fences DRIVE the route.** Every claim below about a `usage_event`
# row is read off the row that a real `POST /v1/audio/transcriptions` wrote,
# never off a row the test inserted. A hand-inserted row proves the column
# exists and proves nothing about the writer.

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

#: The same constant every Console suite uses. `provider_credential` decrypts
#: with the env key at READ time, so a suite that minted its row under a
#: different key would 503 here.
ENC_KEY = "test-encryption-key-not-a-real-one"

#: litellm 1.86.0's `TranscriptionUsageDurationObject`, which reaches the
#: Router only because the Router asked for `verbose_json`.
NINETY_SECONDS = {"type": "duration", "seconds": 90}

TRANSCRIPT = "Sixteen pumps are overdue."

#: 90 seconds of audio, in the unit `task_catalog` prices `transcribe` in.
MINUTES = Decimal("1.5")

#: What the fixture card charges per minute, and what 90 seconds costs at it.
CREDITS_PER_MINUTE = Decimal("0.4")
CALL_COST = Decimal("0.6")

#: A few bytes that stand in for audio. The provider is stubbed, so nothing
#: decodes them — the Router's job is to carry them across unread.
AUDIO = b"RIFF....WAVEfake-audio-bytes"


@pytest.fixture
def provider():
    """The stubbed provider call: what the Router SENT, and what it answers.

    The seam is `set_provider_call`, which exists so the Router can be tested
    without an audio bill at Groq on every run.

    ⚠️ **The original call is restored afterwards.** A stub left installed
    reaches every suite that shares this process.
    """
    state: dict = {
        "calls": [],
        "reply": {"text": TRANSCRIPT, "usage": dict(NINETY_SECONDS)},
    }

    async def _stub(**kwargs):
        state["calls"].append(kwargs)
        return dict(state["reply"])

    original = router_mod._PROVIDER_CALL[0]
    router_mod.set_provider_call(_stub)
    yield state
    router_mod.set_provider_call(original)


@pytest.fixture
def client(monkeypatch, provider):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    """A COMMITTING engine. The sibling `conn` fixture rolls back, and a
    route driven through the app opens its own connection — so a row written
    inside that rollback would be invisible to the route."""
    return create_engine(_URL, future=True)


@pytest.fixture
def org_key(client, db, monkeypatch):
    """A provisioned org, a live key, and a platform Groq credential.

    Groq because `010` binds `tier-stt` to `groq/whisper-large-v3-turbo`.
    """
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    with db.begin() as c:
        ensure_deployment(c)
    slug = f"stt-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    token = client.post(
        "/keys", headers=OP, json={"org_slug": slug}).json()["token"]

    with db.begin() as c:
        c.execute(
            # ⚠️ `provider_credential_live_uniq` admits ONE live platform row
            # per provider, and this fixture runs once per test. Every
            # Console suite mints under the same `ENC_KEY`, so the row an
            # earlier test left behind decrypts here.
            text("INSERT INTO provider_credential (provider, secret_enc, "
                 " label) VALUES ('groq', :s, 'platform') "
                 "ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret("sk-groq-secret")},
        )
    return slug, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org_id(db, org_key):
    slug, _ = org_key
    with db.begin() as c:
        return str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"), {"s": slug}
        ).scalar_one())


@pytest.fixture
def priced_stt(db):
    """A per-MINUTE tier card for `tier-stt`, for the life of one test.

    ⚠️ **Removed afterwards.** Migration 015 seeds no tier rates, and pricing
    the slate is the owner's commercial act (D19.2, H-42). A fixture that
    left its row behind would break the sibling fence whose claim is about
    what the seed ships.
    """
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tier_rate_card (tier, task, unit, credits_per_unit, "
            " input_credits_per_1k, output_credits_per_1k, "
            " cached_input_credits_per_1k, pricing_mode, effective_from) "
            "VALUES ('tier-stt', 'transcribe', 'minutes', :c, 0, 0, 0, "
            " 'priced', now())"), {"c": CREDITS_PER_MINUTE})
    yield
    with db.begin() as c:
        c.execute(text(
            "DELETE FROM tier_rate_card "
            "WHERE tier = 'tier-stt' AND task = 'transcribe'"))


@pytest.fixture
def gate_on(monkeypatch):
    """Turn the CP-6 refusals on. They ship OFF (CLAUDE.md §4, ship dark)."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_SPEND_GATE", "1")


def _transcribe(client, key, *, model="tier-stt", **fields):
    return client.post(
        "/v1/audio/transcriptions", headers=key,
        files={"file": ("meeting.wav", AUDIO, "audio/wav")},
        data={"model": model, **fields},
    )


def _rows(db, org_id: str) -> list:
    """Every `usage_event` this organization has. A fresh org has none, so
    the count is a real assertion rather than a filter."""
    with db.begin() as c:
        return c.execute(text(
            "SELECT task, tier, model, quantity, unit, billed_credits, "
            "       provider_cost_usd, refusal_reason "
            "FROM usage_event WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org_id}).all()


class TestTheModelFieldIsATierAlias:
    """Clause 1. The DOOR declares the task and the field names a TIER."""

    def test_a_bare_model_id_is_refused(self, client, org_key):
        _, key = org_key
        answer = _transcribe(client, key, model=WHISPER)
        assert answer.status_code == 400
        assert "name a tier, not a model" in answer.json()["detail"]

    def test_a_chat_tier_cannot_serve_audio(self, client, org_key):
        """D60.2 in one line. `tier-fast` is bound to `chat` alone, so it
        has no binding on this task and the request stops here."""
        _, key = org_key
        assert _transcribe(client, key, model="tier-fast").status_code == 400

    def test_the_bound_tier_serves(self, client, org_key):
        _, key = org_key
        answer = _transcribe(client, key)
        assert answer.status_code == 200
        assert answer.json() == {"text": TRANSCRIPT}


class TestATranscribeCallNeverStreams:
    """Clause 2. NEW behaviour: `STREAMABLE_TASKS` cannot serve this."""

    def test_a_truthy_stream_field_is_refused(self, client, org_key, provider):
        _, key = org_key
        answer = _transcribe(client, key, stream="true")
        assert answer.status_code == 400
        assert "does not stream" in answer.json()["detail"]
        # Refused BEFORE the provider call. A wall after it would refuse a
        # request we had already paid for.
        assert provider["calls"] == []

    def test_the_other_spellings_of_yes_are_refused_too(self, client, org_key):
        """A form field is a STRING, so every truthy spelling has to be
        named — `bool("false")` is True and would let a stream through."""
        _, key = org_key
        for spelling in ("1", "True", "YES", "on"):
            answer = _transcribe(client, key, stream=spelling)
            assert answer.status_code == 400, spelling

    def test_a_falsy_stream_field_still_serves(self, client, org_key):
        _, key = org_key
        assert _transcribe(client, key, stream="false").status_code == 200


class TestTheRouterSendsVerboseJson:
    """Clause 3. Without this the zero-bill arm is the only arm that runs."""

    def test_a_served_call_sends_verbose_json_upstream(
            self, client, org_key, provider):
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        assert provider["calls"][-1]["response_format"] == "verbose_json"

    def test_the_caller_cannot_change_the_meters_format(
            self, client, org_key, provider):
        """The METER owns the field. A caller who asks for `text` would
        take the duration off every row they wrote."""
        _, key = org_key
        assert _transcribe(
            client, key, response_format="text").status_code == 200
        assert provider["calls"][-1]["response_format"] == "verbose_json"

    def test_the_caller_reads_a_transcript_not_the_verbose_body(
            self, client, org_key, provider):
        """So the meter's choice never changes the customer contract."""
        provider["reply"] = {
            "text": TRANSCRIPT, "usage": dict(NINETY_SECONDS),
            "language": "en", "segments": [{"id": 0, "text": TRANSCRIPT}],
        }
        _, key = org_key
        assert _transcribe(client, key).json() == {"text": TRANSCRIPT}

    def test_the_verb_comes_from_the_capability_row(
            self, client, org_key, provider):
        """Clause 6. D60 step two, on the serving path for the first time."""
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        assert provider["calls"][-1]["invocation"] == "atranscription"

    def test_the_audio_reaches_the_provider_unread(
            self, client, org_key, provider):
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        sent = provider["calls"][-1]["file"]
        assert sent.read() == AUDIO
        # The name carries the FORMAT, and an OpenAI-family provider needs it.
        assert sent.name == "meeting.wav"


class TestTheUsageRowCarriesTheQuantityAndTheUnit:
    """Clause 4. The plumbing this slice built, read off the written row."""

    def test_a_served_call_writes_the_minutes_and_the_unit(
            self, client, db, org_key, org_id, priced_stt, provider):
        _, key = org_key
        assert _transcribe(client, key).status_code == 200

        rows = _rows(db, org_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.task == "transcribe"
        assert row.tier == "tier-stt"
        assert row.model == WHISPER
        assert row.quantity == MINUTES
        assert row.unit == "minutes"
        assert row.refusal_reason is None

    def test_the_minutes_are_billed_at_the_per_minute_card(
            self, client, db, org_key, org_id, priced_stt):
        """1.5 minutes at 0.4 credits a minute. Not a token rate — a minute
        of audio rated per 1k tokens is a number, and a plausible one, and
        wrong."""
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        assert _rows(db, org_id)[0].billed_credits == CALL_COST

    def test_a_token_call_still_writes_a_NULL_quantity(
            self, client, db, org_key, org_id, provider):
        """The hard-coded `quantity=None` is gone, and a chat call must
        still record NULL — the three token columns already carry it."""
        _, key = org_key
        provider["reply"] = {
            "id": "chatcmpl-1", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        # 🔴 **Scoped to THIS organization, never to the platform.**
        # `test_customer_console_router.py` owns the live PLATFORM DeepSeek
        # row and asserts on its secret. Writing one here with
        # `ON CONFLICT DO NOTHING` left that suite reading OUR secret and
        # turned it red — measured 2026-08-31, on a shared scratch database.
        # An organization-scoped row wins the `NULLS LAST` order for this org
        # alone, so the sibling suite sees exactly what it wrote.
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc, "
                " label, organization_id) "
                "VALUES ('deepseek', :s, 'tasks-suite', CAST(:o AS uuid))"),
                {"s": router_mod.encrypt_secret("sk-deepseek"), "o": org_id})
        answer = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "hi"}]})

        assert answer.status_code == 200
        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].quantity is None


class TestAMissingDurationBillsZero:
    """Clause 3's last arm. A completion never fails on metering."""

    def test_no_duration_still_answers_the_caller(
            self, client, org_key, provider):
        provider["reply"] = {"text": TRANSCRIPT}
        _, key = org_key
        answer = _transcribe(client, key)
        assert answer.status_code == 200
        assert answer.json() == {"text": TRANSCRIPT}

    def test_no_duration_bills_zero_and_still_writes_the_row(
            self, client, db, org_key, org_id, priced_stt, provider):
        provider["reply"] = {"text": TRANSCRIPT}
        _, key = org_key
        assert _transcribe(client, key).status_code == 200

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].billed_credits == Decimal(0)
        assert rows[0].quantity == Decimal(0)
        # The unit stays, because the history has to stay readable.
        assert rows[0].unit == "minutes"

    def test_the_older_bare_duration_shape_is_read_too(
            self, client, db, org_key, org_id, priced_stt, provider):
        """litellm copies a bare `duration` across from providers that send
        one. Reading only the usage object would bill those calls zero."""
        provider["reply"] = {"text": TRANSCRIPT, "duration": 30}
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        assert _rows(db, org_id)[0].quantity == Decimal("0.5")

    def test_the_usage_object_wins_over_the_bare_duration(self):
        """It is the REPORTED number rather than the inferred one."""
        answer = router_mod.duration_seconds(
            {"usage": dict(NINETY_SECONDS), "duration": 5})
        assert answer == Decimal(90)

    def test_a_broken_duration_reads_as_unmeasured(self):
        for broken in ({"duration": "loud"}, {"duration": -4},
                       {"duration": True}, {}, None):
            assert router_mod.duration_seconds(broken) is None


class TestAnUnpricedVendorCostStaysNull:
    """Clause 5, and D-AI-7 rule 3. NULL means nobody told us."""

    def test_no_vendor_price_writes_NULL_never_zero(
            self, client, db, org_key, org_id, priced_stt):
        _, key = org_key
        assert _transcribe(client, key).status_code == 200
        assert _rows(db, org_id)[0].provider_cost_usd is None

    def test_an_absent_column_answers_NULL_and_does_not_raise(
            self, conn, monkeypatch):
        """🔴 H-78 builds `vendor_per_minute_usd` and H-46 landed FIRST.

        ⚠️ The scratch database this suite runs on may already carry the
        column, because a sibling branch applied its migration to it. So the
        guard is proved against a column name that certainly does not exist
        rather than against the state of one shared box.
        """
        from customer_console import main as main_mod
        monkeypatch.setattr(main_mod, "_PER_MINUTE_COLUMN", "vendor_per_furlong_usd")

        assert main_mod._vendor_per_minute(conn, WHISPER) is None
        # The transaction survives, which is the whole point of asking the
        # catalog instead of catching the error: a failed statement poisons
        # a Postgres transaction and would take the metering write with it.
        assert conn.execute(text("SELECT 1")).scalar_one() == 1

    def test_zero_minutes_cost_NULL_rather_than_zero_dollars(self):
        """Recording $0.00 for a call we could not read would report a
        margin of 100 percent on a call nobody measured."""
        assert router_mod.vendor_cost_per_minute_usd(
            Decimal(0), Decimal("0.004")) is None
        assert router_mod.vendor_cost_per_minute_usd(
            MINUTES, None) is None
        assert router_mod.vendor_cost_per_minute_usd(
            MINUTES, Decimal("0.004")) == Decimal("0.00600000")


class TestARefusedCallWritesOneRow:
    """Clause 10. The meter records the WALL as well as the call (§8.1)."""

    def test_the_400_writes_a_tier_unknown_row_in_minutes(
            self, client, db, org_key, org_id):
        _, key = org_key
        assert _transcribe(client, key, model="tier-nope").status_code == 400

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "tier_unknown"
        assert rows[0].task == "transcribe"
        assert rows[0].tier == "tier-nope"
        # `_task_unit` reads `task_catalog.natural_unit`, so the refusal row
        # reads beside a served one.
        assert rows[0].unit == "minutes"
        assert rows[0].billed_credits == Decimal(0)

    def test_the_402_writes_an_insufficient_credits_row_in_minutes(
            self, client, db, org_key, org_id, gate_on):
        """A fresh trial organization holds no credits, and the gate stands
        BEFORE the provider call."""
        _, key = org_key
        assert _transcribe(client, key).status_code == 402

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "insufficient_credits"
        assert rows[0].unit == "minutes"

    def test_a_refused_call_never_reaches_the_provider(
            self, client, org_key, gate_on, provider):
        _, key = org_key
        assert _transcribe(client, key).status_code == 402
        assert provider["calls"] == []

    def test_the_401_writes_no_row_at_all(self, client, db):
        """🔴 Structural, not an omission. The key check refuses before the
        code knows the organization, and `usage_event.organization_id` is
        NOT NULL. Do not invent a system organization to make it fit."""
        before = _count_all(db)
        answer = client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": "Bearer cc_live_not_a_key"},
            files={"file": ("m.wav", AUDIO, "audio/wav")},
            data={"model": "tier-stt"})
        assert answer.status_code == 401
        assert _count_all(db) == before


class TestTheProviderCallDispatchesOnTheCapabilityVerb:
    """Clause 6, tested where the stub cannot reach.

    🔴 **Every fence above replaces the WHOLE provider call**, so not one of
    them ever runs the litellm dispatch. A defect there would ship green and
    then answer audio from a chat verb in production, which is D60.2.
    """

    def test_an_unserved_verb_raises_rather_than_defaulting(self):
        """Guessing `acompletion` is how audio reaches a chat endpoint. The
        capability table can legally name a verb no route serves yet."""
        with pytest.raises(router_mod.UnservableInvocation):
            asyncio.run(router_mod._litellm_call(invocation="aspeech"))

    def test_the_named_verb_is_the_one_called(self, monkeypatch):
        import litellm
        seen: dict = {}

        async def _fake(**kwargs):
            seen.update(kwargs)
            return "ok"

        monkeypatch.setattr(litellm, "atranscription", _fake, raising=False)
        answer = asyncio.run(
            router_mod._litellm_call(invocation="atranscription", model="m"))

        assert answer == "ok"
        # The Router's own instruction never reaches litellm or the vendor.
        assert seen == {"model": "m"}

    def test_a_caller_that_names_nothing_gets_acompletion(self, monkeypatch):
        """Every chat call made this exact request before `invocation`
        existed, and the default keeps that path unchanged."""
        import litellm
        called: list = []

        async def _fake(**kwargs):
            called.append(kwargs)
            return "ok"

        monkeypatch.setattr(litellm, "acompletion", _fake, raising=False)
        asyncio.run(router_mod._litellm_call(model="m"))
        assert called == [{"model": "m"}]

    def test_the_operator_vocabulary_is_wider_than_the_serving_one(self):
        """§6A.9 rule 3: capability is not availability. `KNOWN_INVOCATIONS`
        is what an operator may WRITE, and this slice added nothing to it."""
        assert router_mod.SERVING_INVOCATIONS < catalog.KNOWN_INVOCATIONS
        assert "atranscription" in router_mod.SERVING_INVOCATIONS


class TestTheEndpointHasNoTenantCaller:
    """Clause 7. The hop is CP-11's, behind `ROUTER_SERVING_ENABLED`."""

    def test_no_tenant_code_posts_to_the_transcribe_route(self):
        """D57.3's lesson: an endpoint nobody calls is CP-4's mistake. The
        caller slice follows, and until it does this stays true."""
        hits = []
        for base in ("apps", "packages", "workbench/control_plane/src"):
            root = ROOT / base
            for path in root.rglob("*"):
                if path.suffix not in {".py", ".ts", ".tsx"}:
                    continue
                if "customer_console" in path.parts:
                    continue
                if "/v1/audio/transcriptions" in path.read_text(
                        encoding="utf-8", errors="ignore"):
                    hits.append(str(path.relative_to(ROOT)))
        assert hits == []


def _count_all(db) -> int:
    with db.begin() as c:
        return c.execute(
            text("SELECT count(*) FROM usage_event")).scalar_one()


def test_this_suite_is_named_in_the_ci_skip_guard():
    """The hand-maintained list defends itself, the way the others do.

    ⚠️ A new R8-gated Console suite that is NOT named there still runs — and
    still SKIPS silently when the database variable is missing, reporting
    green. That is the CP-3 failure this guard exists to catch, and the guard
    is a hand-list that nothing discovers.
    """
    ci = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_tasks.py" in ci
