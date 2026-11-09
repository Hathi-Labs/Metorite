"""MS-2: connect a provider, adopt its models, Undo, and the H6 key fix.

Spec: ``project-docs/specs/customer_console.md`` §6A.18, the MS-2 row of its
slice table. Board: task 2482 (epic 2458).

🔴 **The done-when, against a real database.**

* A fake key returns 400 and stores nothing.
* With a mocked vendor, adopt declares, tries and then binds. A failed Try
  binds nothing.
* Undo restores the old chain, and answers 409 after a later write.
* A connect with ``org_slug`` binds nothing.

🔴 **The fence:** no response, log line or audit row holds the key, whole,
masked or in part. A vendor's words go through ``_try_provider_text``.

**No network.** Each key check goes to an ``httpx.MockTransport`` through
``key_check.set_transport``, and each Try goes to a ``set_provider_call``
fake. **R8.** The database clauses run against a real Postgres through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
The top half needs no database and always runs.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
import pathlib
import textwrap
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

pytest.importorskip("fastapi")

from customer_console import catalog_health as health
from customer_console import key_check, try_media
from customer_console import provider_registry as registry
from customer_console import router as router_mod
from customer_console.wav import wav_seconds
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_DB = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_SRC = ROOT / "apps/services/customer_console/customer_console/main.py"
KEY_CHECK_SRC = ROOT / "apps/services/customer_console/customer_console/key_check.py"

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
ENC_KEY = "test-encryption-key-not-a-real-one"

#: The label every credential this suite writes carries.
_LABEL = "ms2-connect-fence"

TRANSLIT = "sarvam/saaras:v4/translit"
AAI_EN = "assemblyai/universal-3-5-pro/en"
AAI_HI_EN = "assemblyai/universal-3-5-pro/hi,en"
GROQ_STT = "groq/whisper-large-v3-turbo"
HAIKU = "anthropic/claude-haiku-5-5"
NANO = "assemblyai/nano"
SARVAM_ALIAS = "sarvam.ai/saaras:v4"

#: The models whose capability rows this suite may write. Each is restored.
_TOUCHED_MODELS = (TRANSLIT, AAI_EN, AAI_HI_EN, GROQ_STT, HAIKU, NANO, SARVAM_ALIAS,
                   "gemini/gemini-2.5-flash", "openai/whisper-1")
#: The tiers whose chains this suite may write. Each new set is removed.
_TOUCHED_TIERS = ("tier-stt", "tier-vision", "tier-fast")


def _key(name: str) -> str:
    """A key no vendor word can match by chance: a fixed head, random hex."""
    return f"sk-ms2{name}-{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"


# ── No database: the rules, the scrub, the media and the source ─────────────


class TestTheGuards:
    """Task 2457: each case is refused at declare time, with the fix."""

    @pytest.mark.parametrize(("model", "task", "verb", "rule", "fix"), [
        # A chat verb on a speech model, on a spelling of Sarvam.
        (SARVAM_ALIAS, "transcribe", "acompletion", "H2", (TRANSLIT, "native_sarvam")),
        # A litellm verb tied to the wrong task (task 2457 defect 1).
        ("openai/whisper-1", "transcribe", "acompletion", "H1",
         ("openai/whisper-1", "atranscription")),
        ("openai/gpt-image", "image", "acompletion", "H1",
         ("openai/gpt-image", "aimage_generation")),
        # An async AssemblyAI model the door cannot call (defect 3).
        (NANO, "transcribe", "atranscription", "H4", (AAI_EN, "native_assemblyai")),
        # A provider no handler and no litellm knows (defect 2).
        ("strandco/one", "chat", "acompletion", "H2", None),
    ])
    def test_a_bad_pair_is_refused_with_the_fix(self, model, task, verb, rule, fix):
        refusal = health.pair_refusal(model, task, verb)
        assert refusal is not None
        assert refusal.rule == rule
        assert refusal.fix == fix
        sentence = refusal.sentence(model)
        assert sentence.startswith(refusal.reason)
        if fix is not None:
            assert fix[1] in sentence

    @pytest.mark.parametrize(("model", "task", "verb"), [
        (TRANSLIT, "transcribe", "native_sarvam"),
        # No mode: the handler adds `translit`, so it works (MS-1 as built).
        ("sarvam/saaras:v4", "transcribe", "native_sarvam"),
        (GROQ_STT, "transcribe", "atranscription"),
        ("deepseek/deepseek-chat", "chat", "acompletion"),
        (HAIKU, "vision", "acompletion"),
        ("typesafe/jev-1.13.0", "decide", "native_typesafe"),
    ])
    def test_a_good_pair_passes(self, model, task, verb):
        assert health.pair_refusal(model, task, verb) is None

    def test_every_registry_model_passes_the_guard(self):
        for entry in registry.PROVIDERS:
            for m in entry.models:
                assert health.pair_refusal(m.id, m.job, m.invocation) is None, m.id

    def test_no_key_never_refuses_a_declare(self):
        """H8 is a health rule only. A key can come after the declare."""
        assert health.pair_refusal("mistral/mistral-large-latest", "chat", "acompletion") is None


class TestRuleH7:
    TODAY = date(2026, 11, 9)

    def _report(self, today):
        flash = "gemini/gemini-2.5-flash"
        return health.assess(
            {("tier-fast", "chat"): [flash, "deepseek/deepseek-chat"]},
            {(flash, "chat"): "acompletion"},
            frozenset({"gemini", "deepseek"}),
            today=today,
        )

    def test_a_model_that_retires_within_30_days_gets_its_successor(self):
        report = self._report(self.TODAY)
        (entry,) = report.entries
        assert entry.kind == "H7"
        assert entry.reason == "This model retires on 2026-12-01"
        assert entry.fix.model == "gemini/gemini-3.6-flash"
        assert entry.fix.invocation == "acompletion"
        assert entry.works is True
        (line,) = report.chains
        assert line.window_blocked is False
        assert line.reason == "1 entry on Chat retires soon"

    def test_a_past_date_is_h7_and_a_far_date_is_not(self):
        assert self._report(date(2027, 1, 1)).entries[0].kind == "H7"
        assert self._report(date(2026, 10, 1)).entries == ()
        # No date, no H7: MS-1 callers keep their answer.
        assert self._report(None).entries == ()

    def test_a_retiring_step_still_counts_as_working_in_a_fix(self):
        """A fix of another entry keeps the retiring step first, as a working step."""
        flash, whisper = "gemini/gemini-2.5-flash", "openai/whisper-1"
        models = [flash, whisper]
        report = health.assess(
            {("tier-fast", "chat"): models},
            {(flash, "chat"): "acompletion", (whisper, "chat"): "atranscription"},
            frozenset({"gemini", "openai"}),
            today=self.TODAY,
        )
        plan = health.plan_fix(
            report, tier="tier-fast", task="chat", models=models, picked=[whisper]
        )
        assert plan.chain == (flash, whisper)
        assert plan.declares == ((whisper, "acompletion"),)

    def test_the_connect_flow_never_offers_a_retiring_model(self):
        model = registry.registry_model("gemini/gemini-2.5-flash", "chat")
        assert model is not None and registry.is_retiring(model, self.TODAY)
        assert registry.offer_refusal(model, self.TODAY) == "This model retires on 2026-12-01"


class TestKeyEntries:
    def test_a_key_under_an_alias_is_an_entry_with_no_chain(self):
        (entry,) = health.key_entries(frozenset({"sarvam.ai", "anthropic"}))
        assert (entry.provider, entry.target, entry.action) == ("sarvam.ai", "sarvam", "move_key")
        assert entry.reason == "The key is saved as 'sarvam.ai'"

    def test_the_production_state_revokes_the_alias_only(self):
        (entry,) = health.key_entries(frozenset({"sarvam.ai", "sarvam"}))
        assert entry.action == "revoke_alias"

    def test_a_callable_id_is_never_an_alias(self):
        assert registry.alias_target("sarvam") is None
        assert registry.alias_target("openai") is None
        assert registry.alias_target("no-such-vendor") is None
        assert registry.alias_target("sarvam.ai") == "sarvam"


class TestTheScrub:
    """A vendor's words reach a response only through `_try_provider_text`."""

    def _scrub(self, words: str, secret: str) -> str:
        from customer_console import main

        failed = router_mod.UpstreamFailed(401, vendor="openai")
        failed.__cause__ = Exception(words)
        return main._try_provider_text(
            failed, {"openai": router_mod.Credential(secret, None, False)}
        )

    @pytest.mark.parametrize("shape", ["whole", "masked", "slice", "encoded", "tail-mask"])
    def test_no_form_of_the_key_survives(self, shape):
        import urllib.parse

        secret = _key("scrub")
        words = {
            "whole": f"Incorrect API key provided: {secret}.",
            "masked": f"Incorrect API key provided: {secret[:8]}****{secret[-4:]}.",
            "slice": f"key {secret[6:20]} is not valid",
            "encoded": f"bad key {urllib.parse.quote(secret, safe='')}",
            "tail-mask": f"You passed ****{secret[-6:]} as the key",
        }[shape]
        said = self._scrub(words, secret)
        assert secret not in said
        assert secret[6:20] not in said
        assert f"{secret[:8]}" not in said
        assert secret[-6:] not in said
        assert "[key]" in said

    def test_plain_words_stay(self):
        said = self._scrub("The model `whisper-1` does not exist", _key("plain"))
        assert said == "The model `whisper-1` does not exist"


class TestTheMedia:
    def test_the_clip_is_a_three_second_pcm_wav(self):
        clip = try_media.try_clip()
        assert wav_seconds(clip) is not None
        assert abs(float(wav_seconds(clip)) - try_media.CLIP_SECONDS) < 0.01

    def test_the_image_is_a_png_the_door_reads(self):
        from customer_console.image_sniff import sniff_image

        assert sniff_image(try_media.try_image()) == "image/png"


def _func_node(func):
    return ast.parse(textwrap.dedent(inspect.getsource(func))).body[0]


def _sql_of(func) -> str:
    node = _func_node(func)
    doc = node.body[0]
    return "\n".join(
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n is not getattr(doc, "value", None)
    )


def _names_called(func) -> set[str]:
    return {
        c.func.id for c in ast.walk(_func_node(func))
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }


class TestTheSource:
    def test_the_four_writes_are_admin_with_a_window(self):
        from customer_console.operator_roles import ADMIN, MATRIX

        for path in (
            "/catalog/providers/{provider_id}/connect",
            "/catalog/providers/{provider_id}/adopt",
            "/catalog/adopt/undo",
            "/catalog/health/fix-key",
        ):
            rule = MATRIX[("POST", path)]
            assert rule.min_role == ADMIN and rule.elevated is True, path
        assert MATRIX[("POST", "/catalog/providers/{provider_id}/connect")] == MATRIX[
            ("POST", "/providers/credentials")]

    def test_every_chain_writer_takes_the_lock_and_never_reads_now(self):
        from customer_console import main

        for func in (main._adopt_bind, main.undo_adopt):
            assert "_lock_chain" in _names_called(func), func.__name__
            assert "now()" not in _sql_of(func), func.__name__
        assert "clock_timestamp()" in _sql_of(main.undo_adopt)
        assert "_write_chain" in _names_called(main._adopt_bind)

    def test_connect_and_install_share_one_credential_writer(self):
        from customer_console import main

        assert "_store_credential" in _names_called(main.install_provider_credential)
        assert "_store_credential" in _names_called(main.connect_provider)
        # The H6 fix must never route through the writer that revokes first.
        assert "_store_credential" not in _names_called(main.fix_alias_key)

    def test_connect_takes_no_api_base(self):
        from customer_console import main

        assert "api_base" not in main.ConnectRequest.model_fields
        assert main.ConnectRequest.model_config.get("extra") == "forbid"

    def test_each_offered_job_has_a_try(self):
        from customer_console import main

        assert set(main._TRY_ACTIONS) == set(registry.TRY_JOBS)

    def test_the_key_check_follows_no_redirect_and_reads_the_registry_host(self):
        src = KEY_CHECK_SRC.read_text(encoding="utf-8")
        assert "follow_redirects=False" in src
        assert "spec.url" in src
        assert "api_base" not in src.split('"""', 2)[2]
        for entry in registry.PROVIDERS:
            check = entry.key_check
            assert check.url.startswith("https://"), entry.id
            assert check.auth in {"bearer", "token"} or check.auth.startswith("header:")
            assert check.method in {"GET", "POST"}

    def test_the_tries_never_walk_a_chain(self):
        from customer_console import main

        called = _names_called(main._try_one)
        assert "resolve_chain" not in called
        assert "_try_chain" not in called


class TestARefusalNeedsNoDatabase:
    """Task 2511: a 400 that needs no row is answered before the engine opens.

    Main's push unit job runs with no Console database. A touch of the
    engine fails here, so each refusal below proves its own order.
    """

    @pytest.fixture
    def bare(self, monkeypatch):
        import customer_console.main as console_main
        from fastapi.testclient import TestClient

        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)

        def _no_database():
            raise AssertionError("the route opened the database before it refused")

        monkeypatch.setattr(console_main, "get_engine", _no_database)
        return TestClient(console_main.app)

    @pytest.mark.parametrize(("body", "words"), [
        ({"model": SARVAM_ALIAS, "task": "transcribe", "invocation": "acompletion"},
         "No key or handler matches 'sarvam.ai'. Fix: declare sarvam/saaras:v4/translit "
         "with native_sarvam"),
        ({"model": "openai/whisper-1", "task": "transcribe", "invocation": "acompletion"},
         "This model needs a different call type for Speech to text"),
        ({"model": "strandco/one", "task": "chat", "invocation": "acompletion"},
         "No key or handler matches 'strandco'"),
    ])
    def test_the_declare_guard(self, bare, body, words):
        r = bare.post("/catalog/capabilities", headers=OP, json=body)
        assert r.status_code == 400, r.text
        assert words in r.json()["detail"]

    @pytest.mark.parametrize(("path", "body", "status"), [
        ("/catalog/providers/sarvom/connect", {"secret": "x" * 20}, 400),
        ("/catalog/providers/sarvam/connect", {"secret": "short"}, 400),
        ("/catalog/providers/sarvam/adopt", {"models": [{"id": "sarvam/nope"}]}, 400),
        ("/catalog/health/fix-key", {"provider": "anthropic"}, 400),
        ("/catalog/adopt/undo", {"chains": [
            {"tier": "tier-stt", "task": "transcribe",
             "effective_from": "2026-11-09T10:00:00+00:00"}] * 2}, 400),
    ])
    def test_the_ms2_routes(self, bare, path, body, status):
        r = bare.post(path, headers=OP, json=body)
        assert r.status_code == status, r.text


def test_this_suite_is_named_in_the_ci_skip_guard():
    """A new R8 suite that CI does not name still SKIPS silently and reports
    green. This reads the hand-lists and fails if an entry is gone."""
    for flow in ("pr-check.yml", "deploy.yml"):
        ci = (ROOT / ".github/workflows" / flow).read_text(encoding="utf-8")
        assert "tests/unit/test_catalog_connect_adopt_r8.py" in ci, flow


# ── The database ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _schema():
    if not _URL:
        pytest.skip("CUSTOMER_CONSOLE_DATABASE_URL unset")
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db(_schema):
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


class _Vendor:
    """The mocked vendors: the key checks and the Tries, with no network."""

    def __init__(self) -> None:
        self.good: set[str] = set()
        self.requests: list[httpx.Request] = []
        self.calls: list[dict] = []
        self.fail_try = False
        self.slow = False

    async def check(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.slow:
            await asyncio.sleep(2)
        host = request.url.host
        if host == "api.sarvam.ai":
            key = request.headers.get("api-subscription-key", "")
            if key in self.good:
                return httpx.Response(200, json={"transcript": ""})
            # The vendor echoes the key whole, masked, and in part.
            return httpx.Response(403, json={"message": (
                f"Invalid API key {key}. Masked {key[:8]}****{key[-4:]}. Part {key[6:20]}")})
        if host == "api.anthropic.com":
            key = request.headers.get("x-api-key", "")
            if key in self.good:
                return httpx.Response(200, json={"data": [
                    {"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"},
                    {"id": "claude-next-9"}]})
            return httpx.Response(401, json={"error": {"message": f"invalid x-api-key {key}"}})
        return httpx.Response(404, json={"message": "no such host in this test"})

    async def provider_call(self, **kwargs):
        self.calls.append(kwargs)
        payload = kwargs.get("payload")
        secret = kwargs.get("api_key") or getattr(payload, "api_key", "")
        if self.fail_try:
            raise _VendorError(401, (
                f"Incorrect API key provided: {secret}. You passed "
                f"{secret[:8]}****{secret[-4:]}"))
        if "messages" in kwargs:
            return {"choices": [{"message": {"content": "This test works: a red square."}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 7}}
        return {"text": "namaste duniya, yeh ek test hai"}


class _VendorError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


@pytest.fixture
def vendor():
    state = _Vendor()
    key_check.set_transport(httpx.MockTransport(state.check))
    original = router_mod._PROVIDER_CALL[0]
    router_mod.set_provider_call(state.provider_call)
    yield state
    router_mod.set_provider_call(original)
    key_check.set_transport(None)


@pytest.fixture
def client(monkeypatch, db, vendor):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


class _Rig:
    """The rows one test writes, and the platform keys it hides, put back after."""

    def __init__(self, db) -> None:
        self.db = db
        self.since = datetime.now(UTC) - timedelta(seconds=10)
        self.revoked: list[str] = []
        self.caps: dict[tuple[str, str], tuple[str, bool] | None] = {}
        self.orgs: list[str] = []
        self.secrets: list[str] = []
        with db.begin() as c:
            # Only this test's platform keys are live while it runs.
            self.revoked = [str(r[0]) for r in c.execute(text(
                "UPDATE provider_credential SET revoked_at = now() "
                "WHERE organization_id IS NULL AND revoked_at IS NULL RETURNING id"))]
            for model in _TOUCHED_MODELS:
                for row in c.execute(text(
                        "SELECT task, invocation, streams FROM model_capability WHERE model = :m"),
                        {"m": model}):
                    self.caps[(model, str(row[0]))] = (str(row[1]), bool(row[2]))
                c.execute(text("DELETE FROM model_capability WHERE model = :m"), {"m": model})

    def key(self, provider: str, secret: str, *, org_id: str | None = None,
            api_base: str | None = None) -> str:
        self.secrets.append(secret)
        with self.db.begin() as c:
            return str(c.execute(text(
                "INSERT INTO provider_credential "
                "(provider, secret_enc, label, organization_id, api_base) "
                "VALUES (:p, :s, :l, CAST(:o AS uuid), :b) RETURNING id"),
                {"p": provider, "s": router_mod.encrypt_secret(secret), "l": _LABEL,
                 "o": org_id, "b": api_base}).scalar_one())

    def cap(self, model: str, task: str, verb: str) -> None:
        with self.db.begin() as c:
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation, streams) "
                "VALUES (:m, :t, :v, FALSE) ON CONFLICT (model, task) DO UPDATE "
                "SET invocation = EXCLUDED.invocation"), {"m": model, "t": task, "v": verb})

    def chain(self, tier: str, task: str, models: list[str | None], *, ago: int = 1) -> None:
        when = datetime.now(UTC) - timedelta(seconds=ago)
        with self.db.begin() as c:
            for rank, model in enumerate(models, start=1):
                c.execute(text(
                    "INSERT INTO tier_binding (tier, task, model, rank, effective_from) "
                    "VALUES (:t, :k, :m, :r, :f)"),
                    {"t": tier, "k": task, "m": model, "r": rank, "f": when})

    def org(self) -> tuple[str, str]:
        slug = f"ms2-{uuid.uuid4().hex[:8]}"
        with self.db.begin() as c:
            org_id = str(c.execute(text(
                "INSERT INTO organization (slug, name) VALUES (:s, 'MS2') RETURNING id"),
                {"s": slug}).scalar_one())
        self.orgs.append(org_id)
        return slug, org_id

    def clean(self) -> None:
        with self.db.begin() as c:
            for tier in _TOUCHED_TIERS:
                c.execute(text(
                    "DELETE FROM tier_binding WHERE tier = :t AND effective_from >= :f"),
                    {"t": tier, "f": self.since})
            for model in _TOUCHED_MODELS:
                c.execute(text("DELETE FROM model_capability WHERE model = :m"), {"m": model})
            for (model, task), row in self.caps.items():
                if row is not None:
                    c.execute(text(
                        "INSERT INTO model_capability (model, task, invocation, streams) "
                        "VALUES (:m, :t, :v, :s)"),
                        {"m": model, "t": task, "v": row[0], "s": row[1]})
            c.execute(text("DELETE FROM provider_credential WHERE label = :l"), {"l": _LABEL})
            for org_id in self.orgs:
                c.execute(text(
                    "DELETE FROM provider_credential WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org_id})
                c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                          {"o": org_id})
            for cred in self.revoked:
                c.execute(text(
                    "UPDATE provider_credential SET revoked_at = NULL WHERE id = CAST(:i AS uuid)"),
                    {"i": cred})


@pytest.fixture
def rig(db):
    state = _Rig(db)
    yield state
    state.clean()


def _operator(db, role: str) -> tuple[dict[str, str], str]:
    """An operator holding *role*, with a live session."""
    from customer_console import operator_sessions, store

    email = f"ms2-{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with db.begin() as c:
        row = c.execute(text(
            "INSERT INTO operator (email, role) VALUES (:e, :r) RETURNING id"),
            {"e": email, "r": role}).first()
        store.operator_session_insert(
            c, operator_id=str(row[0]), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12))
    return {"Authorization": f"Bearer {issued.token}"}, email


def _chain(db, tier: str, task: str) -> list[str]:
    from customer_console import store

    with db.begin() as c:
        return store.in_force_chain(c, tier=tier, task=task)


def _audits(db, actor: str) -> list[tuple[str, dict]]:
    with db.begin() as c:
        return [(r[0], r[1]) for r in c.execute(text(
            "SELECT action, detail FROM control_audit WHERE actor = :a ORDER BY created_at"),
            {"a": actor})]


def _live(db, provider: str, *, org_id: str | None = None) -> list[str]:
    with db.begin() as c:
        return [str(r[0]) for r in c.execute(text(
            "SELECT id FROM provider_credential WHERE provider = :p AND revoked_at IS NULL "
            "AND organization_id IS NOT DISTINCT FROM CAST(:o AS uuid)"),
            {"p": provider, "o": org_id})]


def _rows(db, provider: str) -> int:
    with db.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM provider_credential WHERE provider = :p"),
            {"p": provider}).scalar_one())


def _assert_no_key(blob: str, secrets) -> None:
    """The fence: no key whole, masked, or in a slice of 12 characters."""
    for secret in secrets:
        assert secret not in blob
        assert f"****{secret[-4:]}" not in blob
        assert secret[6:18] not in blob
        assert secret[-12:] not in blob


@_DB
class TestConnect:
    def test_a_fake_key_returns_400_and_stores_nothing(self, client, rig, vendor, caplog):
        """Done-when 1. The vendor's words come back, and no part of the key."""
        caplog.set_level(logging.DEBUG)
        headers, email = _operator(rig.db, "admin")
        bad = _key("fake")
        before = _rows(rig.db, "sarvam")
        r = client.post("/catalog/providers/sarvam/connect", headers=headers,
                        json={"secret": bad, "label": _LABEL})
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail.startswith("Sarvam AI did not accept the key: ")
        assert "Invalid API key [key]" in detail
        assert _rows(rig.db, "sarvam") == before
        audits = _audits(rig.db, email)
        assert [a for a, _ in audits] == ["refused"]
        _assert_no_key(r.text, [bad])
        _assert_no_key(json.dumps([d for _, d in audits]), [bad])
        _assert_no_key(caplog.text, [bad])
        # The check went to the registry host, once.
        (request,) = vendor.requests
        assert str(request.url) == "https://api.sarvam.ai/speech-to-text"

    def test_a_good_key_is_stored_and_the_models_come_back(self, client, rig, vendor, caplog):
        caplog.set_level(logging.DEBUG)
        headers, email = _operator(rig.db, "admin")
        good = _key("good")
        vendor.good.add(good)
        r = client.post("/catalog/providers/sarvam/connect", headers=headers,
                        json={"secret": good, "label": _LABEL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "sarvam"
        assert body["scope"] == "platform"
        assert body["rotated"] == 0
        assert body["no_list"] is True
        assert body["note"] == (
            "Sarvam AI publishes no model list. Metorite checks the key with one short "
            "test call.")
        models = {m["id"]: m for m in body["models"] if m["job"] == "transcribe"}
        assert models[TRANSLIT]["status"] == "known"
        assert models[TRANSLIT]["offered"] is True
        assert models[TRANSLIT]["recommended_for"] == ["transcribe"]
        assert {j["job"] for j in body["jobs"]} == {"chat", "transcribe"}

        with rig.db.begin() as c:
            cred = router_mod.provider_credential(c, provider="sarvam")
        assert cred is not None and cred.secret == good and cred.api_base is None
        providers = {p["id"]: p for p in client.get("/catalog/providers", headers=OP).json()[
            "providers"]}
        assert providers["sarvam"]["connected"] is True

        audits = _audits(rig.db, email)
        assert [a for a, _ in audits] == ["provider.credential.install"]
        assert audits[0][1] == {"provider": "sarvam", "label": _LABEL, "rotated": 0,
                                "api_base": None, "via": "connect"}
        _assert_no_key(r.text, [good])
        _assert_no_key(caplog.text, [good])

    def test_a_list_marks_each_model_known_gone_or_unknown(self, client, rig, vendor):
        headers, _ = _operator(rig.db, "admin")
        good = _key("anth")
        vendor.good.add(good)
        rig.secrets.append(good)
        r = client.post("/catalog/providers/claude/connect", headers=headers,
                        json={"secret": good, "label": _LABEL})
        assert r.status_code == 200, r.text
        body = r.json()
        # `claude` is an alias. Connect normalises its path id only.
        assert body["provider"] == "anthropic"
        assert body["no_list"] is False
        status = {(m["id"], m.get("job")): m for m in body["models"]}
        assert status[(HAIKU, "chat")]["status"] == "known"
        assert status[(HAIKU, "vision")]["offered"] is True
        opus = status[("anthropic/claude-opus-5-5", "chat")]
        assert opus["status"] == "gone" and opus["offered"] is False
        assert status[("anthropic/claude-next-9", None)]["status"] == "unknown"
        (request,) = vendor.requests
        assert request.url.host == "api.anthropic.com"
        assert request.headers["anthropic-version"] == "2023-06-01"

    def test_a_connect_with_org_slug_binds_nothing(self, client, rig, vendor):
        """Done-when 4. A BYOK key for one org, and the flow ends at the Key step."""
        headers, _ = _operator(rig.db, "admin")
        slug, org_id = rig.org()
        good = _key("byok")
        vendor.good.add(good)
        rig.secrets.append(good)
        with rig.db.begin() as c:
            bindings = c.execute(text("SELECT count(*) FROM tier_binding")).scalar_one()
        r = client.post("/catalog/providers/sarvam/connect", headers=headers,
                        json={"secret": good, "label": _LABEL, "org_slug": slug})
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "byok"
        assert r.json()["models"] == [] and r.json()["jobs"] == []
        assert len(_live(rig.db, "sarvam", org_id=org_id)) == 1
        assert _live(rig.db, "sarvam") == []
        with rig.db.begin() as c:
            assert c.execute(text("SELECT count(*) FROM tier_binding")).scalar_one() == bindings
        providers = {p["id"]: p for p in client.get("/catalog/providers", headers=OP).json()[
            "providers"]}
        assert providers["sarvam"]["connected"] is False

    def test_an_unknown_id_names_the_closest(self, client, rig):
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvom/connect", headers=headers,
                        json={"secret": _key("x"), "label": _LABEL})
        assert r.status_code == 400
        assert "The closest is 'sarvam'" in r.json()["detail"]

    def test_connect_takes_no_api_base(self, client, rig, vendor):
        headers, _ = _operator(rig.db, "admin")
        secret = _key("base")
        r = client.post("/catalog/providers/sarvam/connect", headers=headers, json={
            "secret": secret, "api_base": "https://evil.example"})
        assert r.status_code == 422
        assert vendor.requests == []
        _assert_no_key(r.text, [secret])

    def test_a_vendor_that_does_not_answer_in_time_stores_nothing(
        self, client, rig, vendor, monkeypatch
    ):
        monkeypatch.setattr(key_check, "LIMIT_SECONDS", 0.2)
        vendor.slow = True
        headers, _ = _operator(rig.db, "admin")
        good = _key("slow")
        vendor.good.add(good)
        r = client.post("/catalog/providers/sarvam/connect", headers=headers,
                        json={"secret": good, "label": _LABEL})
        assert r.status_code == 400, r.text
        # A timeout says nothing about the key, so the words do not blame it.
        assert r.json()["detail"] == (
            "Metorite could not check the key, and stored nothing: Sarvam AI did not "
            "answer in 0.2 seconds")
        assert _live(rig.db, "sarvam") == []


def _seed_stt(rig, *, chain: list[str]) -> str:
    """A platform AssemblyAI key, its declares, and a tier-stt chain."""
    aai = _key("aai")
    rig.key("assemblyai", aai)
    for model in chain:
        rig.cap(model, "transcribe", "native_assemblyai" if model.startswith("assemblyai/")
                else "atranscription")
    if chain:
        rig.chain("tier-stt", "transcribe", chain)
    return aai


@_DB
class TestAdopt:
    def test_adopt_declares_tries_then_binds(self, client, rig, vendor, caplog):
        """Done-when 2, the passing half."""
        caplog.set_level(logging.DEBUG)
        _seed_stt(rig, chain=[AAI_EN])
        sarvam = _key("sv")
        rig.key("sarvam", sarvam)
        headers, email = _operator(rig.db, "admin")
        with rig.db.begin() as c:
            usage = c.execute(text("SELECT count(*) FROM usage_event")).scalar_one()
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["declared"] == [
            {"model": TRANSLIT, "job": "transcribe", "invocation": "native_sarvam"}]
        (tried,) = body["tries"]
        assert tried == {"job": "transcribe", "model": TRANSLIT, "ok": True,
                         "said": "namaste duniya, yeh ek test hai", "error": None}
        (bound,) = body["bound"]
        assert bound["was"] == [AAI_EN]
        assert bound["chain"] == [TRANSLIT, AAI_EN]
        assert bound["pushed_out"] == []
        assert _chain(rig.db, "tier-stt", "transcribe") == [TRANSLIT, AAI_EN]
        assert body["undo"] == [{"tier": "tier-stt", "task": "transcribe",
                                 "effective_from": bound["effective_from"]}]

        # The Try called the NEW model alone, with the platform key: no old
        # step answered in its place.
        (call,) = vendor.calls
        assert call["model"] == TRANSLIT
        assert call["invocation"] == "native_sarvam"
        assert call["payload"].api_key == sarvam
        assert call["payload"].organization_id is None

        # Declare, then Try, then bind: the audit rows in that order.
        actions = [a for a, _ in _audits(rig.db, email)]
        assert actions == ["catalog.capability", "catalog.transcribe_try", "catalog.binding"]
        audits = dict(_audits(rig.db, email))
        assert audits["catalog.transcribe_try"]["via"] == "adopt"
        assert audits["catalog.transcribe_try"]["outcome"] == "served"
        assert audits["catalog.binding"]["via"] == "adopt"
        assert audits["catalog.binding"]["was"] == [AAI_EN]
        with rig.db.begin() as c:
            assert c.execute(text("SELECT count(*) FROM usage_event")).scalar_one() == usage
        _assert_no_key(r.text + json.dumps(list(audits.values())) + caplog.text, rig.secrets)

    def test_a_failed_try_binds_nothing(self, client, rig, vendor, caplog):
        """Done-when 2, the failing half. The model stays declared and unbound."""
        caplog.set_level(logging.DEBUG)
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        vendor.fail_try = True
        headers, email = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
        assert r.status_code == 200, r.text
        body = r.json()
        (tried,) = body["tries"]
        assert tried["ok"] is False
        assert tried["error"].startswith("Incorrect API key provided: [key]")
        assert body["bound"] == [] and body["undo"] == []
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]
        with rig.db.begin() as c:
            assert c.execute(text(
                "SELECT invocation FROM model_capability WHERE model = :m AND task = 'transcribe'"),
                {"m": TRANSLIT}).scalar_one() == "native_sarvam"
        actions = [a for a, _ in _audits(rig.db, email)]
        assert actions == ["catalog.capability", "catalog.transcribe_try"]
        assert dict(_audits(rig.db, email))["catalog.transcribe_try"]["outcome"] == (
            "upstream_failed")
        _assert_no_key(r.text + json.dumps(_audits(rig.db, email)) + caplog.text, rig.secrets)

    def test_no_platform_key_is_a_failed_try_and_no_vendor_call(self, client, rig, vendor):
        _seed_stt(rig, chain=[AAI_EN])
        _, org_id = rig.org()
        rig.key("sarvam", _key("byok"), org_id=org_id)  # BYOK never counts
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
        assert r.status_code == 200, r.text
        (tried,) = r.json()["tries"]
        assert tried["ok"] is False
        assert tried["error"] == "No platform key sits under 'sarvam'. Connect the provider first"
        assert vendor.calls == []
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]

    def test_with_no_tiers_a_working_job_is_left_alone(self, client, rig, vendor):
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skipped"] == [{"model": TRANSLIT, "job": "transcribe",
                                    "reason": "Speech to text already works with AssemblyAI"}]
        assert body["declared"] == [] and body["tries"] == [] and body["bound"] == []
        assert vendor.calls == []

    def test_with_no_tiers_a_dead_job_gets_its_tier(self, client, rig, vendor):
        # The production fault: tier-stt holds one step that cannot work.
        rig.key("sarvam", _key("sv"))
        rig.cap(NANO, "transcribe", "atranscription")
        rig.key("assemblyai", _key("aai"))
        rig.chain("tier-stt", "transcribe", [NANO])
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT}]})
        assert r.status_code == 200, r.text
        assert _chain(rig.db, "tier-stt", "transcribe") == [TRANSLIT, NANO]

    def test_a_prepend_names_a_working_step_it_pushes_out_of_the_window(
        self, client, rig, vendor
    ):
        rig.key("groq", _key("groq"))
        _seed_stt(rig, chain=[AAI_EN, AAI_HI_EN, GROQ_STT])
        rig.key("sarvam", _key("sv"))
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
        assert r.status_code == 200, r.text
        (bound,) = r.json()["bound"]
        assert bound["chain"] == [TRANSLIT, AAI_EN, AAI_HI_EN, GROQ_STT]
        assert bound["pushed_out"] == [GROQ_STT]
        assert f"'{GROQ_STT}' is now past the first 3 steps" in bound["note"]

    def test_a_dry_run_writes_nothing_and_calls_nobody(self, client, rig, vendor):
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        headers, email = _operator(rig.db, "admin")
        r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
            "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}], "dry_run": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert body["chains"] == [{"tier": "tier-stt", "task": "transcribe",
                                   "was": [AAI_EN], "chain": [TRANSLIT, AAI_EN]}]
        assert body["tries"] == [{"job": "transcribe", "model": TRANSLIT}]
        assert vendor.calls == []
        assert _audits(rig.db, email) == []
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]
        with rig.db.begin() as c:
            assert c.execute(text("SELECT count(*) FROM model_capability WHERE model = :m"),
                             {"m": TRANSLIT}).scalar_one() == 0

    @pytest.mark.parametrize(("provider", "pick", "words"), [
        ("sarvam", {"id": "sarvam/not-a-model", "tiers": ["tier-stt"]}, "not in Metorite's list"),
        ("sarvam", {"id": TRANSLIT, "tiers": ["tier-ms2-ghost"]}, "names no job"),
        ("sarvam", {"id": TRANSLIT, "tiers": ["tier-vision"]}, "does not do Image understanding"),
        ("gemini", {"id": "gemini/gemini-3.1-flash-image", "tiers": ["tier-image"]},
         "Metorite has no test for Image creation yet"),
        ("gemini", {"id": "gemini/gemini-2.5-flash", "tiers": ["tier-fast"]},
         "This model retires on 2026-12-01"),
    ])
    def test_a_pick_the_flow_does_not_offer_is_a_400(
        self, client, rig, vendor, provider, pick, words
    ):
        headers, _ = _operator(rig.db, "admin")
        r = client.post(f"/catalog/providers/{provider}/adopt", headers=headers,
                        json={"models": [pick]})
        assert r.status_code == 400, r.text
        assert words in r.json()["detail"]
        assert vendor.calls == []

    def test_a_lock_wait_is_a_409_and_binds_nothing(self, client, rig, vendor, monkeypatch):
        from customer_console import main

        monkeypatch.setattr(main, "_FIX_LOCK_TIMEOUT", "200ms")
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        headers, _ = _operator(rig.db, "admin")
        holder = rig.db.connect()
        tx = holder.begin()
        try:
            holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                           {"k": main._chain_lock_key("tier-stt", "transcribe")})
            r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
                "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
        finally:
            tx.rollback()
            holder.close()
        assert r.status_code == 409, r.text
        assert "Try again" in r.json()["detail"]
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]


def _adopt(client, headers) -> dict:
    r = client.post("/catalog/providers/sarvam/adopt", headers=headers, json={
        "models": [{"id": TRANSLIT, "tiers": ["tier-stt"]}]})
    assert r.status_code == 200, r.text
    return r.json()


@_DB
class TestUndo:
    def test_undo_restores_the_old_chain(self, client, rig, vendor):
        """Done-when 3, the first half."""
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        headers, email = _operator(rig.db, "admin")
        adopted = _adopt(client, headers)
        assert _chain(rig.db, "tier-stt", "transcribe") == [TRANSLIT, AAI_EN]
        r = client.post("/catalog/adopt/undo", headers=headers,
                        json={"chains": adopted["undo"]})
        assert r.status_code == 200, r.text
        (undone,) = r.json()["undone"]
        assert undone["chain"] == [AAI_EN]
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]
        binding = [d for a, d in _audits(rig.db, email) if a == "catalog.binding"][-1]
        assert binding["via"] == "undo"
        assert binding["chain"] == [AAI_EN]
        assert binding["was"] == [TRANSLIT, AAI_EN]
        # A declare stays: Undo restores a chain, never a verb.
        with rig.db.begin() as c:
            assert c.execute(text("SELECT count(*) FROM model_capability WHERE model = :m"),
                             {"m": TRANSLIT}).scalar_one() == 1
        # The chain moved again (the Undo itself), so a second Undo is a 409.
        again = client.post("/catalog/adopt/undo", headers=headers,
                            json={"chains": adopted["undo"]})
        assert again.status_code == 409

    def test_undo_answers_409_after_a_later_write(self, client, rig, vendor):
        """Done-when 3, the second half. Compare and set on effective_from."""
        _seed_stt(rig, chain=[AAI_EN])
        rig.key("sarvam", _key("sv"))
        headers, _ = _operator(rig.db, "admin")
        adopted = _adopt(client, headers)
        later = client.post("/catalog/bindings", headers=OP, json={
            "tier": "tier-stt", "task": "transcribe", "models": [AAI_EN, TRANSLIT]})
        assert later.status_code == 200, later.text
        r = client.post("/catalog/adopt/undo", headers=headers,
                        json={"chains": adopted["undo"]})
        assert r.status_code == 409, r.text
        assert "changed after the adopt" in r.json()["detail"]
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN, TRANSLIT]

    def test_undo_of_a_first_bind_takes_the_tier_off_the_air(self, client, rig, vendor):
        rig.key("sarvam", _key("sv"))
        # tier-stt has no chain from this test. A tombstone hides any older set.
        rig.chain("tier-stt", "transcribe", [None], ago=3)
        headers, _ = _operator(rig.db, "admin")
        adopted = _adopt(client, headers)
        assert _chain(rig.db, "tier-stt", "transcribe") == [TRANSLIT]
        r = client.post("/catalog/adopt/undo", headers=headers,
                        json={"chains": adopted["undo"]})
        assert r.status_code == 200, r.text
        assert r.json()["undone"][0]["chain"] == []
        with rig.db.begin() as c, pytest.raises(router_mod.TierUnknown):
            router_mod.resolve_chain(c, "tier-stt", "transcribe")

    @pytest.mark.parametrize("chains", [
        [{"tier": "tier-stt", "task": "transcribe", "effective_from": "2026-11-09T10:00:00"}],
        [],
        [{"tier": "tier-stt", "task": "transcribe",
          "effective_from": "2026-11-09T10:00:00+00:00", "x": 1}],
    ])
    def test_bad_input_is_a_422(self, client, rig, chains):
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/adopt/undo", headers=headers, json={"chains": chains})
        assert r.status_code == 422, r.text

    def test_a_chain_named_twice_is_a_400(self, client, rig):
        headers, _ = _operator(rig.db, "admin")
        one = {"tier": "tier-stt", "task": "transcribe",
               "effective_from": "2026-11-09T10:00:00+00:00"}
        r = client.post("/catalog/adopt/undo", headers=headers, json={"chains": [one, one]})
        assert r.status_code == 400


@_DB
class TestTheKeyFix:
    """Rule H6, the button. The fix never revokes a fresh key."""

    def test_the_production_state_revokes_the_alias_only(self, client, rig, vendor):
        """2026-11-09: a `sarvam` row copied by hand, and the live `sarvam.ai` row."""
        fresh, old = _key("fresh"), _key("old")
        fresh_id = rig.key("sarvam", fresh)
        rig.key("sarvam.ai", old)
        headers, email = _operator(rig.db, "admin")
        keys = client.get("/catalog/health", headers=headers).json()["keys"]
        assert keys == [{
            "kind": "H6", "provider": "sarvam.ai", "target": "sarvam",
            "reason": "The key is saved as 'sarvam.ai', and a key under 'sarvam' exists too",
            "fix": {"action": "revoke_alias", "label": "Revoke the key under 'sarvam.ai'"},
        }]
        r = client.post("/catalog/health/fix-key", headers=headers,
                        json={"provider": "sarvam.ai"})
        assert r.status_code == 200, r.text
        assert r.json() == {"from": "sarvam.ai", "to": "sarvam", "action": "revoked_alias"}
        assert _live(rig.db, "sarvam") == [fresh_id]
        assert _live(rig.db, "sarvam.ai") == []
        with rig.db.begin() as c:
            assert router_mod.provider_credential(c, provider="sarvam").secret == fresh
        assert vendor.requests == []  # no key check: nothing was copied
        (move,) = [d for a, d in _audits(rig.db, email) if a == "provider.credential.move"]
        assert move["action"] == "revoked_alias"
        assert client.get("/catalog/health", headers=headers).json()["keys"] == []
        _assert_no_key(r.text + json.dumps(_audits(rig.db, email)), [fresh, old])

    def test_the_copy_checks_the_old_key_then_moves_it(self, client, rig, vendor, caplog):
        caplog.set_level(logging.DEBUG)
        old = _key("old")
        vendor.good.add(old)
        rig.key("sarvam.ai", old, api_base="https://sarvam.example")
        headers, email = _operator(rig.db, "admin")
        r = client.post("/catalog/health/fix-key", headers=headers,
                        json={"provider": "sarvam.ai"})
        assert r.status_code == 200, r.text
        assert r.json()["action"] == "copied"
        # The key check went to the REGISTRY host, never to the row's api_base.
        (request,) = vendor.requests
        assert str(request.url) == "https://api.sarvam.ai/speech-to-text"
        with rig.db.begin() as c:
            moved = router_mod.provider_credential(c, provider="sarvam")
        assert moved.secret == old
        assert moved.api_base == "https://sarvam.example"
        assert moved.byok is False
        assert _live(rig.db, "sarvam.ai") == []
        (move,) = [d for a, d in _audits(rig.db, email) if a == "provider.credential.move"]
        assert move == {"from": "sarvam.ai", "to": "sarvam", "action": "copied",
                        "label": _LABEL, "api_base": "https://sarvam.example"}
        _assert_no_key(r.text + json.dumps(_audits(rig.db, email)) + caplog.text, [old])

    def test_an_old_key_that_fails_the_check_moves_nothing(self, client, rig, vendor):
        old = _key("dead")
        alias_id = rig.key("sarvam.ai", old)
        headers, email = _operator(rig.db, "admin")
        r = client.post("/catalog/health/fix-key", headers=headers,
                        json={"provider": "sarvam.ai"})
        assert r.status_code == 400, r.text
        assert "Connect Sarvam AI again with a new key" in r.json()["detail"]
        assert _live(rig.db, "sarvam.ai") == [alias_id]
        assert _live(rig.db, "sarvam") == []
        _assert_no_key(r.text + json.dumps(_audits(rig.db, email)), [old])

    def test_a_key_saved_during_the_check_is_never_revoked(
        self, client, rig, vendor, monkeypatch
    ):
        """A connect lands while the fix waits on the vendor. The fresh key wins."""
        old, fresh = _key("old"), _key("fresh")
        rig.key("sarvam.ai", old)
        landed: list[str] = []

        def _connect_lands(entry, secret):
            assert secret == old and entry.id == "sarvam"
            landed.append(rig.key("sarvam", fresh))
            return key_check.KeyResult(True, 200, "", None)

        monkeypatch.setattr(key_check, "check", _connect_lands)
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/health/fix-key", headers=headers,
                        json={"provider": "sarvam.ai"})
        assert r.status_code == 200, r.text
        assert r.json()["action"] == "revoked_alias"
        assert _live(rig.db, "sarvam") == landed
        with rig.db.begin() as c:
            assert router_mod.provider_credential(c, provider="sarvam").secret == fresh

    @pytest.mark.parametrize(("provider", "status"), [
        ("anthropic", 400), ("no-such-vendor", 400), ("assembly.ai", 409)])
    def test_a_key_that_is_no_live_alias_is_refused(self, client, rig, provider, status):
        headers, _ = _operator(rig.db, "admin")
        r = client.post("/catalog/health/fix-key", headers=headers, json={"provider": provider})
        assert r.status_code == status, r.text


@_DB
class TestTheGuardsOnTheRoutes:
    def test_a_declare_of_the_production_pair_is_refused_with_the_fix(self, client, rig):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": SARVAM_ALIAS, "task": "transcribe", "invocation": "acompletion"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == (
            "No key or handler matches 'sarvam.ai'. Fix: declare "
            "sarvam/saaras:v4/translit with native_sarvam")
        with rig.db.begin() as c:
            assert c.execute(text("SELECT count(*) FROM model_capability WHERE model = :m"),
                             {"m": SARVAM_ALIAS}).scalar_one() == 0

    def test_a_litellm_verb_on_the_wrong_task_is_refused(self, client, rig):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": "openai/whisper-1", "task": "transcribe", "invocation": "acompletion"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == (
            "This model needs a different call type for Speech to text. Fix: declare it "
            "with atranscription")

    def test_a_bind_refuses_a_bad_pair_it_adds(self, client, rig):
        rig.cap(NANO, "transcribe", "atranscription")  # written before the guard existed
        rig.cap(AAI_EN, "transcribe", "native_assemblyai")
        rig.chain("tier-stt", "transcribe", [AAI_EN])
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": "tier-stt", "task": "transcribe", "models": [AAI_EN, NANO]})
        assert r.status_code == 400, r.text
        assert r.json()["detail"].startswith(f"'{NANO}' on 'transcribe': Metorite cannot call")
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN]

    def test_a_bind_keeps_a_dead_step_that_is_already_on_the_air(self, client, rig):
        rig.cap(NANO, "transcribe", "atranscription")
        rig.cap(AAI_EN, "transcribe", "native_assemblyai")
        rig.chain("tier-stt", "transcribe", [NANO])
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": "tier-stt", "task": "transcribe", "models": [AAI_EN, NANO]})
        assert r.status_code == 200, r.text
        assert _chain(rig.db, "tier-stt", "transcribe") == [AAI_EN, NANO]


@_DB
class TestTheHealthAdditions:
    def test_h7_reaches_the_health_list(self, client, rig):
        flash = "gemini/gemini-2.5-flash"
        rig.key("gemini", _key("gem"))
        rig.cap(flash, "chat", "acompletion")
        rig.chain("tier-fast", "chat", [flash])
        body = client.get("/catalog/health", headers=OP).json()
        (entry,) = [e for e in body["entries"] if e["tier"] == "tier-fast"]
        assert entry["kind"] == "H7"
        assert entry["fix"]["model"] == "gemini/gemini-3.6-flash"

    def test_a_dead_step_a_byok_customer_uses_says_so_before_the_fix(self, client, rig):
        _, org_id = rig.org()
        rig.key("groq", _key("byok"), org_id=org_id)
        rig.cap(GROQ_STT, "transcribe", "atranscription")
        _seed_stt(rig, chain=[GROQ_STT, AAI_EN])
        body = client.get("/catalog/health", headers=OP).json()
        (entry,) = [e for e in body["entries"] if e["model"] == GROQ_STT]
        assert entry["kind"] == "H8"
        assert entry["byok_orgs"] == 1
        assert entry["byok_note"].startswith("1 organization uses its own key")


@_DB
class TestTheGate:
    ROUTES = (
        ("/catalog/providers/sarvam/connect", {"secret": "x" * 20}),
        ("/catalog/providers/sarvam/adopt", {"models": [{"id": TRANSLIT}]}),
        ("/catalog/adopt/undo", {"chains": [{
            "tier": "tier-stt", "task": "transcribe",
            "effective_from": "2026-11-09T10:00:00+00:00"}]}),
        ("/catalog/health/fix-key", {"provider": "sarvam.ai"}),
    )

    @pytest.mark.parametrize("role", ["viewer", "editor"])
    def test_only_an_admin_reaches_the_four_writes(self, client, rig, vendor, role):
        headers, _ = _operator(rig.db, role)
        for path, body in self.ROUTES:
            assert client.post(path, headers=headers, json=body).status_code == 403, path
        assert vendor.requests == [] and vendor.calls == []

    def test_with_elevation_on_an_admin_needs_a_window(self, client, rig, vendor, monkeypatch):
        from customer_console.operator_elevation import ELEVATION_FLAG

        monkeypatch.setenv(ELEVATION_FLAG, "1")
        headers, _ = _operator(rig.db, "admin")
        for path, body in self.ROUTES:
            assert client.post(path, headers=headers, json=body).status_code == 403, path
        assert vendor.requests == []
