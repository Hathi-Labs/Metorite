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

#: The Router's own token — the only credential that may write the meter.
INT = {"Authorization": "Bearer internal"}

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


def _transcribe(client, key, *, model="tier-stt", audio=AUDIO,
                filename="meeting.wav", headers=None, **fields):
    return client.post(
        "/v1/audio/transcriptions", headers={**key, **(headers or {})},
        files={"file": (filename, audio, "audio/wav")},
        data={"model": model, **fields},
    )


# ── §6A.10c: the image door and the speak door ──────────────────────────────
#
# 🔴 **THE SLICE SEEDS NO `tier_binding` ROW** (clause 4). Which vendor model
# we resell for pictures and for speech is a COMMERCIAL decision, and it
# belongs to the owner. So every fence that needs a bound tier seeds its OWN
# binding here and DELETES it again, and the fences pass against an unbound
# database.

#: ⚠️ **Two vendors no sibling Console suite touches**, because
#: `provider_credential_live_uniq` admits ONE live platform row per provider
#: and a suite that minted a second would collide with whoever owns the first.
IMAGE_MODEL = "stability/stable-image-core"
SPEECH_MODEL = "elevenlabs/eleven-multilingual-v2"

#: What the caller asks for, and what the stubbed provider answers with. The
#: two disagree on purpose — clause 5 bills what came BACK.
IMAGES_ASKED = 3
IMAGES_RETURNED = 2

#: A known input string, and the character count clause 6 measures off it.
SPOKEN = "Sixteen pumps are overdue."
CHARACTERS = Decimal(len(SPOKEN))

#: The audio a stubbed speech provider answers with, and its own type.
SPEECH_BYTES = b"ID3\x04fake-mp3-bytes"
SPEECH_MEDIA_TYPE = "audio/mpeg"

#: What the fixture cards charge, and what one stubbed call costs at them.
CREDITS_PER_IMAGE = Decimal("2.5")
IMAGE_CALL_COST = Decimal(5)
CREDITS_PER_CHARACTER = Decimal("0.01")
SPEECH_CALL_COST = (CHARACTERS * CREDITS_PER_CHARACTER)


def _image_reply(count: int = IMAGES_RETURNED) -> dict:
    """The OpenAI image shape, with ``count`` pictures in it."""
    return {"created": 1, "data": [{"url": f"https://x/{i}.png"}
                                   for i in range(count)]}


def _speech_reply() -> dict:
    """What the provider seam answers for a speech call — bytes and a type."""
    return {"content": SPEECH_BYTES, "media_type": SPEECH_MEDIA_TYPE}


def _generate(client, key, *, model="tier-image", n=IMAGES_ASKED, **fields):
    body = {"model": model, "prompt": "a red bicycle", **fields}
    if n is not None:
        body["n"] = n
    return client.post("/v1/images/generations", headers=key, json=body)


def _speak(client, key, *, model="tier-tts", text_=SPOKEN, **fields):
    return client.post("/v1/audio/speech", headers=key, json={
        "model": model, "input": text_, "voice": "alloy", **fields})


#: The label every credential these fixtures mint carries, so the teardown
#: removes ITS OWN row and never a row a sibling suite owns.
_FENCE_LABEL = "media-fence"


def _bind(db, *, tier, model, task, invocation, streams=False):
    """Seed ONE binding, ONE capability and ONE vendor key. Clause 4.

    ⚠️ **The credential is the PLATFORM row, never an organization one.** An
    organization-scoped key IS BYOK (§3.4), and a BYOK call is metered and
    billed ZERO — so every price this suite asserts would read zero for the
    wrong reason. Measured on the first run of these fences.
    """
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tier_binding (tier, model, task, effective_from) "
            "VALUES (:t, :m, :k, '2026-01-01T00:00:00Z')"),
            {"t": tier, "m": model, "k": task})
        c.execute(text(
            "INSERT INTO model_capability (model, task, invocation, streams) "
            "VALUES (:m, :k, :i, :s)"),
            {"m": model, "k": task, "i": invocation, "s": streams})
        c.execute(text(
            "INSERT INTO provider_credential (provider, secret_enc, label) "
            "VALUES (:p, :s, :l) ON CONFLICT DO NOTHING"),
            {"p": model.split("/", 1)[0], "l": _FENCE_LABEL,
             "s": router_mod.encrypt_secret("sk-media-fence")})


def _unbind(db, *, tier, model):
    """Take every row :func:`_bind` wrote back out again.

    🔴 **The fixture cleans up after ITSELF.** `tier_binding` and
    `model_capability` are global rows, so one left behind would make a
    sibling fence — *"neither route serves without a binding"* — pass for the
    wrong reason on the next run.
    """
    with db.begin() as c:
        c.execute(text("DELETE FROM tier_binding WHERE tier = :t"), {"t": tier})
        c.execute(text("DELETE FROM model_capability WHERE model = :m"),
                  {"m": model})
        c.execute(text(
            "DELETE FROM provider_credential "
            "WHERE provider = :p AND label = :l"),
            {"p": model.split("/", 1)[0], "l": _FENCE_LABEL})


@pytest.fixture
def bound_image(db, org_key, provider):
    """`tier-image` bound, capable and credentialled, for ONE test."""
    provider["reply"] = _image_reply()
    _bind(db, tier="tier-image", model=IMAGE_MODEL, task="image",
          invocation="aimage_generation")
    yield
    _unbind(db, tier="tier-image", model=IMAGE_MODEL)


@pytest.fixture
def bound_tts(db, org_key, provider):
    """`tier-tts` bound, capable and credentialled, for ONE test."""
    provider["reply"] = _speech_reply()
    _bind(db, tier="tier-tts", model=SPEECH_MODEL, task="speak",
          invocation="aspeech")
    yield
    _unbind(db, tier="tier-tts", model=SPEECH_MODEL)


@pytest.fixture
def streaming_tts(db, org_key, provider):
    """The same binding, with `streams = TRUE` on the capability row.

    🔴 **Clause 3's named path.** `speak` IS in `STREAMABLE_TASKS`, so an
    operator MAY write this row. Nothing on the serving path reads it, and
    this fixture exists to prove that.
    """
    provider["reply"] = _speech_reply()
    _bind(db, tier="tier-tts", model=SPEECH_MODEL, task="speak",
          invocation="aspeech", streams=True)
    yield
    _unbind(db, tier="tier-tts", model=SPEECH_MODEL)


def _price(db, *, tier, task, unit, per_unit):
    """A per-UNIT tier card, for the life of one test.

    ⚠️ **Removed afterwards**, for the reason `priced_stt` gives: migration
    015 seeds no tier rates, and pricing the slate is the owner's commercial
    act (H-42).
    """
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tier_rate_card (tier, task, unit, credits_per_unit, "
            " input_credits_per_1k, output_credits_per_1k, "
            " cached_input_credits_per_1k, pricing_mode, effective_from) "
            "VALUES (:t, :k, :u, :c, 0, 0, 0, 'priced', now())"),
            {"t": tier, "k": task, "u": unit, "c": per_unit})


def _unprice(db, *, tier, task):
    with db.begin() as c:
        c.execute(text(
            "DELETE FROM tier_rate_card WHERE tier = :t AND task = :k"),
            {"t": tier, "k": task})


@pytest.fixture
def priced_image(db):
    _price(db, tier="tier-image", task="image", unit="images",
           per_unit=CREDITS_PER_IMAGE)
    yield
    _unprice(db, tier="tier-image", task="image")


@pytest.fixture
def priced_tts(db):
    _price(db, tier="tier-tts", task="speak", unit="characters",
           per_unit=CREDITS_PER_CHARACTER)
    yield
    _unprice(db, tier="tier-tts", task="speak")


@pytest.fixture
def minute_priced_profile(db):
    """A vendor profile priced on the MINUTE column ALONE. Fence row 7.

    The measured defect: `_record_completion` read the per-minute column for
    every call that carried a quantity, so this profile would have costed an
    image call at a price for one minute of audio.
    """
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO model_profile (model, vendor_per_minute_usd) "
            "VALUES (:m, 0.004) "
            "ON CONFLICT (model) DO UPDATE SET vendor_per_minute_usd = 0.004"),
            {"m": IMAGE_MODEL})
    yield
    with db.begin() as c:
        c.execute(text("DELETE FROM model_profile WHERE model = :m"),
                  {"m": IMAGE_MODEL})


def _grant(client, slug: str, credits: str):
    return client.post("/credits/grant", headers=OP,
                       json={"org_slug": slug, "credits": credits})


def _charge(client, org_id: str, credits: str, run_id: str):
    """Draw a RUN down through the REAL metering path, never by editing it.

    The internal token is the Router's own, and a customer key deliberately
    cannot reach it. Seeding the run this way asserts the ceiling rather than
    the test's patience — 500 credits is 388 stubbed completions.
    """
    return client.post("/usage/record", headers=INT, json={
        "organization_id": org_id,
        "request_id": f"seed-{uuid.uuid4().hex}",
        "billed_credits": credits, "run_id": run_id, "model": "m"})


_ROW_COLUMNS = (
    "SELECT task, tier, model, quantity, unit, billed_credits, "
    "       provider_cost_usd, refusal_reason, run_id "
    "FROM usage_event WHERE organization_id = CAST(:o AS uuid)"
)


def _rows(db, org_id: str) -> list:
    """Every `usage_event` this organization has. A fresh org has none, so
    the count is a real assertion rather than a filter."""
    with db.begin() as c:
        return c.execute(text(_ROW_COLUMNS), {"o": org_id}).all()


def _refusals(db, org_id: str) -> list:
    """Only the rows that record a WALL.

    ⚠️ Needed where the test SEEDS spend through `/usage/record`, which
    writes a served row of its own. Counting every row would then count the
    seed and say nothing about the refusal writer.
    """
    with db.begin() as c:
        return c.execute(
            text(_ROW_COLUMNS + " AND refusal_reason IS NOT NULL"),
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
        monkeypatch.setitem(
            main_mod._PER_UNIT_COLUMNS, "minutes", "vendor_per_furlong_usd")

        assert main_mod._vendor_per_unit(conn, WHISPER, unit="minutes") is None
        # The transaction survives, which is the whole point of asking the
        # catalog instead of catching the error: a failed statement poisons
        # a Postgres transaction and would take the metering write with it.
        assert conn.execute(text("SELECT 1")).scalar_one() == 1

    def test_a_unit_no_column_prices_answers_NULL(self, conn):
        """`tokens` and `seconds` have no per-unit column, and neither does
        a task nobody has told us about. NULL, never zero."""
        from customer_console import main as main_mod
        assert main_mod._vendor_per_unit(conn, WHISPER, unit="tokens") is None
        assert main_mod._vendor_per_unit(conn, WHISPER, unit=None) is None

    def test_zero_minutes_cost_NULL_rather_than_zero_dollars(self):
        """Recording $0.00 for a call we could not read would report a
        margin of 100 percent on a call nobody measured."""
        assert router_mod.vendor_cost_per_unit_usd(
            Decimal(0), Decimal("0.004")) is None
        assert router_mod.vendor_cost_per_unit_usd(
            MINUTES, None) is None
        assert router_mod.vendor_cost_per_unit_usd(
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

    def test_the_403_writes_a_run_ceiling_row_in_minutes(
            self, client, db, org_key, org_id, gate_on):
        """The THIRD wall clause 10 names. §4.4's circuit breaker.

        The balance is deliberately not the issue — 10,000 credits granted,
        so a 402 here would be the wrong refusal. The RUN is spent to its
        ceiling instead, which is the tripwire on one loop.
        """
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        answer = _transcribe(client, key, headers={"X-CC-Run": "run-hot"})

        assert answer.status_code == 403, answer.text
        assert answer.json()["detail"]["reason"] == "run_ceiling_exceeded"

        rows = _refusals(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "run_ceiling_exceeded"
        assert rows[0].task == "transcribe"
        assert rows[0].unit == "minutes"
        # A `run_ceiling_exceeded` row without its run is not actionable, and
        # the breaker reads the same field.
        assert rows[0].run_id == "run-hot"
        assert rows[0].billed_credits == Decimal(0)

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


class TestTheUploadCeilingAndTheEmptyFile:
    """What the route does with the BODY, measured rather than assumed.

    ⚠️ **The 413 bounds what we SEND, not what we accept.** Starlette parses
    the whole multipart body into the `UploadFile` dependency before this
    handler runs, and it spools above 1 MB to disk. So an over-large body is
    read in full and then refused. §6A.10a records that bound and names the
    acceptance-side cap as follow-up work for the owner.
    """

    def test_an_empty_file_is_forwarded_and_not_refused(
            self, client, org_key, provider):
        """🔴 DELIBERATE, and pinned so a change is visible.

        The provider answers for empty audio, and the meter bills the zero
        duration it reports. The Router does not decode audio, so refusing
        here would mean guessing what is inside a file we never read.
        """
        _, key = org_key
        answer = _transcribe(client, key, audio=b"", filename="silence.wav")

        assert answer.status_code == 200
        assert provider["calls"][-1]["file"].read() == b""

    def test_an_empty_file_bills_the_duration_the_provider_reports(
            self, client, db, org_key, org_id, priced_stt, provider):
        provider["reply"] = {"text": "", "usage": {"type": "duration",
                                                   "seconds": 0}}
        _, key = org_key
        assert _transcribe(client, key, audio=b"").status_code == 200

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].quantity == Decimal(0)
        assert rows[0].billed_credits == Decimal(0)

    def test_an_oversize_upload_is_refused_413(
            self, client, org_key, monkeypatch, provider):
        from customer_console import main as main_mod
        # The ceiling is lowered rather than a 26 MB body sent, because the
        # subject is the REFUSAL and not the parser's throughput.
        monkeypatch.setattr(main_mod, "_MAX_AUDIO_BYTES", 8)

        _, key = org_key
        answer = _transcribe(client, key)

        assert answer.status_code == 413
        assert "is refused" in answer.json()["detail"]
        assert provider["calls"] == []

    def test_the_413_writes_no_usage_row(
            self, client, db, org_key, org_id, monkeypatch):
        """Migration 020's CHECK holds three slugs. A fourth spelling minted
        for this wall is the thing §8.1 forbids."""
        from customer_console import main as main_mod
        monkeypatch.setattr(main_mod, "_MAX_AUDIO_BYTES", 8)

        _, key = org_key
        assert _transcribe(client, key).status_code == 413
        assert _rows(db, org_id) == []

    def test_the_body_read_comes_BEFORE_the_stream_check(
            self, client, org_key, monkeypatch):
        """📌 The ORDER, pinned. An over-large `stream=true` request answers
        413 and never 400, because the handler cannot see the form field
        until the body it rides on is already parsed."""
        from customer_console import main as main_mod
        monkeypatch.setattr(main_mod, "_MAX_AUDIO_BYTES", 8)

        _, key = org_key
        answer = _transcribe(client, key, stream="true")

        assert answer.status_code == 413

    def test_a_body_under_the_ceiling_but_over_the_spool_still_serves(
            self, client, org_key, provider):
        """Starlette spools above 1 MB to disk, and the route reads it back.
        A cap that only worked in memory would refuse real audio."""
        _, key = org_key
        big = b"x" * (2 * 1024 * 1024)
        assert _transcribe(client, key, audio=big).status_code == 200
        assert len(provider["calls"][-1]["file"].read()) == len(big)


# ══ §6A.10c — the image endpoint and the speak endpoint ═════════════════════
#
# 🔴 **These fences DRIVE the two routes.** Every claim about a `usage_event`
# row is read off the row a real POST wrote. A hand-inserted row proves the
# column exists and proves nothing about the writer.


class TestTheMediaModelFieldIsATierAlias:
    """Fence row 1. The DOOR declares the task, and the field names a TIER."""

    def test_a_bare_model_id_is_refused_on_the_image_route(
            self, client, org_key):
        _, key = org_key
        answer = _generate(client, key, model=IMAGE_MODEL)
        assert answer.status_code == 400
        assert "name a tier, not a model" in answer.json()["detail"]

    def test_a_bare_model_id_is_refused_on_the_speak_route(
            self, client, org_key):
        _, key = org_key
        answer = _speak(client, key, model=SPEECH_MODEL)
        assert answer.status_code == 400
        assert "name a tier, not a model" in answer.json()["detail"]

    def test_a_chat_tier_cannot_make_a_picture(self, client, org_key):
        """D60.2 in one line. `tier-fast` is bound to `chat` alone."""
        _, key = org_key
        assert _generate(client, key, model="tier-fast").status_code == 400

    def test_a_chat_tier_cannot_speak(self, client, org_key):
        _, key = org_key
        assert _speak(client, key, model="tier-fast").status_code == 400

    def test_the_bound_image_tier_serves(self, client, org_key, bound_image):
        _, key = org_key
        answer = _generate(client, key)
        assert answer.status_code == 200, answer.text
        assert len(answer.json()["data"]) == IMAGES_RETURNED

    def test_the_bound_speech_tier_serves(self, client, org_key, bound_tts):
        _, key = org_key
        answer = _speak(client, key)
        assert answer.status_code == 200, answer.text
        assert answer.content == SPEECH_BYTES

    def test_each_route_calls_the_verb_its_capability_row_names(
            self, client, org_key, bound_image, provider):
        """Clause 9. Dispatch through the ONE seam, on the serving path."""
        _, key = org_key
        assert _generate(client, key).status_code == 200
        assert provider["calls"][-1]["invocation"] == "aimage_generation"

    def test_the_speak_route_calls_aspeech(
            self, client, org_key, bound_tts, provider):
        _, key = org_key
        assert _speak(client, key).status_code == 200
        assert provider["calls"][-1]["invocation"] == "aspeech"


class TestASpeakCallNeverStreams:
    """Fence row 2, and the CONDITION §6A.10c clause 3 now records."""

    def test_a_truthy_stream_field_is_refused(
            self, client, org_key, bound_tts, provider):
        """A D16 agent default: the answer §6A.10a clause 2 already gives on
        the transcribe door, on a third door."""
        _, key = org_key
        answer = _speak(client, key, stream=True)
        assert answer.status_code == 400
        assert "does not stream" in answer.json()["detail"]
        # Refused BEFORE the provider call. A wall after it would refuse a
        # request we had already paid for.
        assert provider["calls"] == []

    def test_the_stream_400_writes_no_usage_row(
            self, client, db, org_key, org_id, bound_tts):
        """Migration 020's CHECK holds three slugs. A fourth spelling minted
        for this wall is the thing §8.1 forbids."""
        _, key = org_key
        assert _speak(client, key, stream=True).status_code == 400
        assert _rows(db, org_id) == []

    def test_a_false_stream_field_still_serves(
            self, client, org_key, bound_tts):
        _, key = org_key
        assert _speak(client, key, stream=False).status_code == 200

    def test_a_capability_that_CLAIMS_to_stream_changes_nothing(
            self, client, db, org_id, org_key, streaming_tts, priced_tts):
        """🔴 Clause 3's named path, DRIVEN.

        `speak` is in `STREAMABLE_TASKS`, so `check_streams` lets an operator
        write `streams = TRUE` on a `speak` capability row. Nothing on the
        serving path reads that column — the chat route branches on the
        request body instead. So the route answers ONE buffered body.
        """
        _, key = org_key
        answer = _speak(client, key)

        assert answer.status_code == 200
        assert answer.content == SPEECH_BYTES
        assert answer.headers["content-type"] == SPEECH_MEDIA_TYPE
        # Not an SSE body: no frames, no sentinel.
        assert b"data:" not in answer.content
        # And it metered exactly once, as a buffered call does.
        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].unit == "characters"

    def test_the_operator_may_still_write_that_row(self):
        """The other half: `check_streams` is unchanged, and this slice did
        not narrow the OPERATOR's vocabulary to make the route safe."""
        assert catalog.check_streams("speak", True) is True
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_streams("image", True)


class TestAnImageRowRecordsThePicturesTheProviderReturned:
    """Fence rows 3 and 5. Clause 5 measures the RESPONSE, never the `n`."""

    def test_two_pictures_for_a_request_that_asked_for_three(
            self, client, db, org_key, org_id, bound_image, priced_image):
        _, key = org_key
        answer = _generate(client, key, n=IMAGES_ASKED)

        assert answer.status_code == 200
        rows = _rows(db, org_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.task == "image"
        assert row.tier == "tier-image"
        assert row.model == IMAGE_MODEL
        # 🔴 TWO, not THREE. The customer holds two pictures.
        assert row.quantity == Decimal(IMAGES_RETURNED)
        assert row.unit == "images"
        assert row.refusal_reason is None

    def test_the_pictures_are_billed_at_the_per_image_card(
            self, client, db, org_key, org_id, bound_image, priced_image):
        _, key = org_key
        assert _generate(client, key).status_code == 200
        assert _rows(db, org_id)[0].billed_credits == IMAGE_CALL_COST

    def test_the_request_n_reaches_the_provider_and_not_the_meter(
            self, client, db, org_key, org_id, bound_image, priced_image,
            provider):
        """The caller's `n` is a request to the vendor. It is never a count
        of what we deliver."""
        _, key = org_key
        assert _generate(client, key, n=IMAGES_ASKED).status_code == 200
        assert provider["calls"][-1]["n"] == IMAGES_ASKED
        assert _rows(db, org_id)[0].quantity == Decimal(IMAGES_RETURNED)

    def test_a_response_with_no_image_list_bills_zero(
            self, client, db, org_key, org_id, bound_image, priced_image,
            provider):
        """Fence row 5. An unreadable count bills zero and does NOT fail the
        call — the customer already holds whatever came back."""
        provider["reply"] = {"created": 1}
        _, key = org_key
        answer = _generate(client, key)

        assert answer.status_code == 200
        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].quantity == Decimal(0)
        assert rows[0].billed_credits == Decimal(0)
        # The unit stays, because the history has to stay readable.
        assert rows[0].unit == "images"

    def test_a_broken_image_list_reads_as_unmeasured(self):
        """The reader itself, where the stub cannot reach."""
        for broken in ({"data": "two"}, {"data": None}, {}, None, "ok"):
            assert router_mod.image_count(broken) is None
        assert router_mod.image_count({"data": []}) == Decimal(0)
        assert router_mod.image_count(_image_reply(5)) == Decimal(5)


class TestASpeechRowRecordsTheCharactersWeSent:
    """Fence row 4. Clause 6 measures the REQUEST, at the opposite end."""

    def test_a_served_call_writes_the_characters_and_the_unit(
            self, client, db, org_key, org_id, bound_tts, priced_tts):
        _, key = org_key
        assert _speak(client, key).status_code == 200

        rows = _rows(db, org_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.task == "speak"
        assert row.tier == "tier-tts"
        assert row.model == SPEECH_MODEL
        assert row.quantity == CHARACTERS
        assert row.unit == "characters"
        assert row.refusal_reason is None

    def test_the_characters_are_billed_at_the_per_character_card(
            self, client, db, org_key, org_id, bound_tts, priced_tts):
        _, key = org_key
        assert _speak(client, key).status_code == 200
        assert _rows(db, org_id)[0].billed_credits == SPEECH_CALL_COST

    def test_the_text_we_SEND_is_the_text_we_COUNT(
            self, client, db, org_key, org_id, bound_tts, priced_tts,
            provider):
        """Never a figure the caller reports, and never the audio body — an
        audio body reports nothing we can count."""
        longer = SPOKEN * 3
        _, key = org_key
        assert _speak(client, key, text_=longer).status_code == 200
        assert provider["calls"][-1]["input"] == longer
        assert _rows(db, org_id)[0].quantity == Decimal(len(longer))

    def test_an_empty_input_bills_zero(
            self, client, db, org_key, org_id, bound_tts, priced_tts):
        """There is no text to count, so the row bills zero and says so."""
        _, key = org_key
        assert _speak(client, key, text_="").status_code == 200

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].quantity == Decimal(0)
        assert rows[0].billed_credits == Decimal(0)
        assert rows[0].unit == "characters"

    def test_the_caller_reads_the_providers_own_bytes_and_type(
            self, client, org_key, bound_tts, provider):
        """Clause 2. Audio bytes, and not JSON."""
        provider["reply"] = {"content": b"OggS-not-mp3",
                             "media_type": "audio/ogg"}
        _, key = org_key
        answer = _speak(client, key)

        assert answer.content == b"OggS-not-mp3"
        assert answer.headers["content-type"] == "audio/ogg"

    def test_a_provider_that_names_no_type_still_plays(self):
        """The reader itself. A body with no type makes a browser guess."""
        assert router_mod.speech_audio({"content": b"x"}) == (
            b"x", router_mod.DEFAULT_SPEECH_MEDIA_TYPE)
        assert router_mod.speech_audio(b"raw") == (
            b"raw", router_mod.DEFAULT_SPEECH_MEDIA_TYPE)
        assert router_mod.speech_audio(None) == (
            b"", router_mod.DEFAULT_SPEECH_MEDIA_TYPE)


class TestTheVendorCostReadsTheColumnForTheTASKS_UNIT:
    """Fence rows 6 and 7. Clause 8's measured defect, repaired."""

    def test_no_vendor_price_writes_NULL_never_zero(
            self, client, db, org_key, org_id, bound_image, priced_image):
        """Fence row 6, and D-AI-7 rule 3. NULL means nobody told us."""
        _, key = org_key
        assert _generate(client, key).status_code == 200
        assert _rows(db, org_id)[0].provider_cost_usd is None

    def test_an_image_call_never_costs_off_the_MINUTE_column(
            self, client, db, org_key, org_id, bound_image, priced_image,
            minute_priced_profile):
        """🔴 Fence row 7, and the whole reason clause 8 calls the old branch
        a LIVE mis-costing.

        The profile carries a per-MINUTE price and no per-image one. The old
        branch multiplied two pictures by a price for one minute of audio.
        The repaired branch reads `task_catalog.natural_unit`, finds
        `images`, reads `vendor_per_image_usd`, and answers NULL.
        """
        _, key = org_key
        assert _generate(client, key).status_code == 200
        assert _rows(db, org_id)[0].provider_cost_usd is None

    def test_a_priced_image_column_DOES_cost_the_call(
            self, client, db, org_key, org_id, bound_image, priced_image):
        """The other half: the right column is read, so a priced one bills.

        Without this the fence above would pass on a branch that costs
        nothing at all.
        """
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO model_profile (model, vendor_per_image_usd) "
                "VALUES (:m, 0.04) ON CONFLICT (model) DO UPDATE SET "
                "vendor_per_image_usd = 0.04"), {"m": IMAGE_MODEL})
        try:
            _, key = org_key
            assert _generate(client, key).status_code == 200
            assert _rows(db, org_id)[0].provider_cost_usd == Decimal(
                "0.08000000")
        finally:
            with db.begin() as c:
                c.execute(text("DELETE FROM model_profile WHERE model = :m"),
                          {"m": IMAGE_MODEL})

    def test_a_speech_call_costs_off_the_CHARACTER_column(
            self, client, db, org_key, org_id, bound_tts, priced_tts):
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO model_profile (model, vendor_per_character_usd, "
                " vendor_per_minute_usd) VALUES (:m, 0.000015, 0.004) "
                "ON CONFLICT (model) DO UPDATE SET "
                " vendor_per_character_usd = 0.000015, "
                " vendor_per_minute_usd = 0.004"), {"m": SPEECH_MODEL})
        try:
            _, key = org_key
            assert _speak(client, key).status_code == 200
            expected = (CHARACTERS * Decimal("0.000015")).quantize(
                Decimal("0.00000001"))
            assert _rows(db, org_id)[0].provider_cost_usd == expected
        finally:
            with db.begin() as c:
                c.execute(text("DELETE FROM model_profile WHERE model = :m"),
                          {"m": SPEECH_MODEL})

    def test_a_transcribe_call_still_costs_off_the_MINUTE_column(
            self, client, db, org_key, org_id, priced_stt):
        """The repair must not move the door it was written behind."""
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO model_profile (model, vendor_per_minute_usd) "
                "VALUES (:m, 0.004) ON CONFLICT (model) DO UPDATE SET "
                "vendor_per_minute_usd = 0.004"), {"m": WHISPER})
        try:
            _, key = org_key
            assert _transcribe(client, key).status_code == 200
            assert _rows(db, org_id)[0].provider_cost_usd == Decimal(
                "0.00600000")
        finally:
            with db.begin() as c:
                c.execute(text("DELETE FROM model_profile WHERE model = :m"),
                          {"m": WHISPER})


class TestNeitherMediaRouteServesWithoutABinding:
    """Fence row 8. Clause 4's wall, on the SHIPPED state of the database."""

    def test_the_image_route_refuses_and_writes_one_refusal_row(
            self, client, db, org_key, org_id):
        """`015` registers `tier-image` and `016` maps it to `image`. No row
        binds a MODEL to it, because that is the owner's commercial act."""
        _, key = org_key
        answer = _generate(client, key)

        assert answer.status_code == 400
        assert "tier-image" in answer.json()["detail"]
        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "tier_unknown"
        assert rows[0].task == "image"
        assert rows[0].tier == "tier-image"
        assert rows[0].unit == "images"
        assert rows[0].billed_credits == Decimal(0)

    def test_the_speak_route_refuses_and_writes_one_refusal_row(
            self, client, db, org_key, org_id):
        _, key = org_key
        answer = _speak(client, key)

        assert answer.status_code == 400
        assert "tier-tts" in answer.json()["detail"]
        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "tier_unknown"
        assert rows[0].task == "speak"
        assert rows[0].tier == "tier-tts"
        assert rows[0].unit == "characters"

    def test_the_ladder_binds_neither_tier(self, conn):
        """🔴 Read off the ladder, so a seed added later goes red HERE.

        Clause 4 forbids this slice from seeding either binding. `010:212`
        seeded `tier-stt` and no such row exists for these two.
        """
        bound = {r[0] for r in conn.execute(text(
            "SELECT tier FROM tier_binding "
            "WHERE tier IN ('tier-image', 'tier-tts')"))}
        assert bound == set()


class TestEachMediaWallWritesOneRowWithItsOwnUnit:
    """Fence row 9. Clause 10, on the second door and the third."""

    def test_the_image_402_writes_an_insufficient_credits_row_in_images(
            self, client, db, org_key, org_id, bound_image, gate_on):
        _, key = org_key
        assert _generate(client, key).status_code == 402

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "insufficient_credits"
        assert rows[0].task == "image"
        assert rows[0].unit == "images"

    def test_the_speak_402_writes_an_insufficient_credits_row_in_characters(
            self, client, db, org_key, org_id, bound_tts, gate_on):
        _, key = org_key
        assert _speak(client, key).status_code == 402

        rows = _rows(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "insufficient_credits"
        assert rows[0].task == "speak"
        assert rows[0].unit == "characters"

    def test_the_image_403_writes_a_run_ceiling_row_in_images(
            self, client, db, org_key, org_id, bound_image, gate_on):
        """The THIRD wall clause 10 names. §4.4's circuit breaker."""
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-pics")

        # The run travels in a header, exactly as it does on the chat door.
        answer = client.post("/v1/images/generations",
                             headers={**key, "X-CC-Run": "run-pics"},
                             json={"model": "tier-image", "prompt": "p"})

        assert answer.status_code == 403, answer.text
        assert answer.json()["detail"]["reason"] == "run_ceiling_exceeded"
        rows = _refusals(db, org_id)
        assert len(rows) == 1
        assert rows[0].task == "image"
        assert rows[0].unit == "images"
        assert rows[0].run_id == "run-pics"

    def test_the_speak_403_writes_a_run_ceiling_row_in_characters(
            self, client, db, org_key, org_id, bound_tts, gate_on):
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-voice")

        answer = client.post("/v1/audio/speech",
                             headers={**key, "X-CC-Run": "run-voice"},
                             json={"model": "tier-tts", "input": SPOKEN,
                                   "voice": "alloy"})

        assert answer.status_code == 403, answer.text
        rows = _refusals(db, org_id)
        assert len(rows) == 1
        assert rows[0].refusal_reason == "run_ceiling_exceeded"
        assert rows[0].task == "speak"
        assert rows[0].unit == "characters"
        assert rows[0].run_id == "run-voice"

    def test_a_refused_media_call_never_reaches_the_provider(
            self, client, org_key, bound_image, gate_on, provider):
        _, key = org_key
        assert _generate(client, key).status_code == 402
        assert provider["calls"] == []

    def test_the_401_writes_no_row_on_either_route(self, client, db):
        """🔴 Structural, not an omission. The key check refuses before the
        code knows the organization, and `usage_event.organization_id` is
        NOT NULL."""
        before = _count_all(db)
        bad = {"Authorization": "Bearer cc_live_not_a_key"}
        assert client.post("/v1/images/generations", headers=bad, json={
            "model": "tier-image", "prompt": "p"}).status_code == 401
        assert client.post("/v1/audio/speech", headers=bad, json={
            "model": "tier-tts", "input": "hi",
            "voice": "alloy"}).status_code == 401
        assert _count_all(db) == before


class TestTheMediaBodiesAreAnAllowlist:
    """Clause 1 and clause 2. Nothing the caller sent reaches the provider
    except the named fields."""

    def test_an_unnamed_field_is_refused(self, client, org_key):
        _, key = org_key
        answer = client.post("/v1/images/generations", headers=key, json={
            "model": "tier-image", "prompt": "p", "api_base": "http://evil"})
        assert answer.status_code == 422

    def test_the_api_base_is_ours_alone(
            self, client, org_key, bound_image, provider):
        _, key = org_key
        assert _generate(client, key).status_code == 200
        # No `api_base` on the credential, so none on the wire.
        assert "api_base" not in provider["calls"][-1]

    def test_the_speak_body_carries_the_three_fields_clause_2_names(
            self, client, org_key, bound_tts, provider):
        _, key = org_key
        assert _speak(client, key).status_code == 200
        sent = provider["calls"][-1]
        assert sent["model"] == SPEECH_MODEL
        assert sent["input"] == SPOKEN
        assert sent["voice"] == "alloy"

    def test_the_picture_count_is_bounded(self, client, org_key):
        """R7 for an AGENT DEFAULT the twelve clauses do not state.

        `n` multiplies what one request costs us, so `_MAX_IMAGES_PER_CALL`
        reuses `CompletionRequest.n`'s own ceiling and its argument — fifty
        pictures would be a 50x draw on the provider account from one
        zero-balance trial call. The owner may overrule the number.
        """
        from customer_console import main as main_mod
        assert main_mod._MAX_IMAGES_PER_CALL == 4
        _, key = org_key
        over = _generate(client, key, n=main_mod._MAX_IMAGES_PER_CALL + 1)
        assert over.status_code == 422

    def test_the_spoken_text_is_bounded(self, client, org_key):
        """R7 for the second AGENT DEFAULT, anchored on the vendor.

        The OpenAI speech endpoint refuses an input above 4096 characters,
        and the Router holds the text in memory to replay it across a
        failover. `_MAX_AUDIO_BYTES` states its own ceiling the same way.
        """
        from customer_console import main as main_mod
        assert main_mod._MAX_SPEECH_CHARACTERS == 4096
        _, key = org_key
        over = _speak(client, key,
                      text_="x" * (main_mod._MAX_SPEECH_CHARACTERS + 1))
        assert over.status_code == 422


class TestTheProviderCallDispatchesOnTheCapabilityVerb:
    """Clause 6, tested where the stub cannot reach.

    🔴 **Every fence above replaces the WHOLE provider call**, so not one of
    them ever runs the litellm dispatch. A defect there would ship green and
    then answer audio from a chat verb in production, which is D60.2.
    """

    def test_an_unserved_verb_raises_rather_than_defaulting(self):
        """Guessing `acompletion` is how audio reaches a chat endpoint. The
        capability table can legally name a verb no route serves yet.

        ⚠️ `aembedding`, because §6A.10c clause 9 gave `aspeech` a door and
        the Router may call it now. `aembedding` still has none, so it is
        what an operator may WRITE and the Router may not CALL.
        """
        with pytest.raises(router_mod.UnservableInvocation):
            asyncio.run(router_mod._litellm_call(invocation="aembedding"))

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
        is what an operator may WRITE, and this slice added nothing to it.

        🔴 **§6A.10c clause 9's fence, row 10.** The two media verbs joined
        the SERVING set, and the STRICT subset holds: `aembedding` has no
        door, so the two sets must never become one.
        """
        assert router_mod.SERVING_INVOCATIONS < catalog.KNOWN_INVOCATIONS
        assert "atranscription" in router_mod.SERVING_INVOCATIONS
        assert "aimage_generation" in router_mod.SERVING_INVOCATIONS
        assert "aspeech" in router_mod.SERVING_INVOCATIONS
        # The gap is what makes the two sets two. `catalog.check_invocation`
        # accepts this verb and `_litellm_call` refuses it.
        assert "aembedding" not in router_mod.SERVING_INVOCATIONS


class TestTheEndpointHasNoTenantCaller:
    """Clause 7. The hop is CP-11's, behind `ROUTER_SERVING_ENABLED`."""

    @pytest.mark.parametrize("route", [
        "/v1/audio/transcriptions",
        "/v1/images/generations",
        "/v1/audio/speech",
    ])
    def test_no_tenant_code_posts_to_the_serving_route(self, route):
        """D57.3's lesson: an endpoint nobody calls is CP-4's mistake. The
        caller slice follows, and until it does this stays true.

        §6A.10c clause 3 names "no tenant caller" for both media routes, so
        all three doors carry the same claim.
        """
        hits = []
        for base in ("apps", "packages", "workbench/control_plane/src"):
            root = ROOT / base
            for path in root.rglob("*"):
                if path.suffix not in {".py", ".ts", ".tsx"}:
                    continue
                if "customer_console" in path.parts:
                    continue
                if route in path.read_text(
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
