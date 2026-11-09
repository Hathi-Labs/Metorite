"""MS-1: catalog health finds the steps that cannot work, and fixes them.

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "Health rules" and the
MS-1 row of its slice table. Board: task 2481 (epic 2458).

🔴 **The done-when, against a real database.** The production state of
2026-11-08 gives three Speech to text entries:

* ``assemblyai/nano`` gets the fix ``assemblyai/universal-3-5-pro/hi,en`` on
  ``native_assemblyai``;
* ``assemblyai/best`` shows "Remove: same as the fix above";
* ``sarvam.ai/saaras:v4`` shows the H6 reason and no model fix;
* ``anthropic/claude-haiku-4-5`` on ``tier-vision`` gives no entry.

A BYOK key under ``sarvam`` sits in the fixture too. It is not "connected",
and it gives the Sarvam entry no fix, because only a PLATFORM key counts.

The fix route takes the chain lock, writes in one transaction, inserts a new
chain row (never an update), audits in the bind's shape, and answers 409 on
a lock wait. No secret reaches a response, a log line or an audit row.

**R8.** The database clauses run against a real Postgres through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
The top half needs no database and always runs.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from customer_console import catalog_health as health
from customer_console import router as router_mod
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

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
ENC_KEY = "test-encryption-key-not-a-real-one"

#: The label every credential this suite writes carries.
_LABEL = "ms1-health-fence"

SARVAM_ALIAS = "sarvam.ai/saaras:v4"
NANO = "assemblyai/nano"
BEST = "assemblyai/best"
HAIKU = "anthropic/claude-haiku-4-5"
U35 = "assemblyai/universal-3-5-pro/hi,en"

#: The production keys of 2026-11-08: platform rows, exact provider strings.
PROD_KEYS = ("anthropic", "assemblyai", "deepseek", "sarvam.ai", "aimlapi")

#: The production chains and capability rows of 2026-11-08.
PROD_CHAINS = {
    ("tier-stt", "transcribe"): [SARVAM_ALIAS, NANO, BEST],
    ("tier-vision", "vision"): [HAIKU],
}
PROD_CAPS = {
    (SARVAM_ALIAS, "transcribe"): "acompletion",
    (NANO, "transcribe"): "atranscription",
    (BEST, "transcribe"): "atranscription",
    (HAIKU, "vision"): "acompletion",
}


def _secret(provider: str) -> str:
    return f"sk-ms1-{provider.replace('.', '-')}-0123456789abcdef"


BYOK_SECRET = "sk-ms1-byok-sarvam-0123456789abcdef"


def _stt(entries):
    return [e for e in entries if e["tier"] == "tier-stt" and e["task"] == "transcribe"]


def _vision(entries):
    return [e for e in entries if e["tier"] == "tier-vision"]


def _assert_production_entries(entries) -> None:
    """The done-when, one clause a line."""
    stt = {e["model"]: e for e in _stt(entries)}
    assert list(stt) == [SARVAM_ALIAS, NANO, BEST]

    nano = stt[NANO]
    assert nano["fix"]["action"] == "replace"
    assert nano["fix"]["model"] == U35
    assert nano["fix"]["invocation"] == "native_assemblyai"
    assert nano["help"] is None

    best = stt[BEST]
    assert best["fix"]["action"] == "remove"
    assert best["fix"]["label"] == "Remove: same as the fix above"
    assert best["fix"]["same_as"] == NANO

    sarvam = stt[SARVAM_ALIAS]
    assert sarvam["kind"] == "H6"
    assert sarvam["reason"] == "The key is saved as 'sarvam.ai'"
    assert sarvam["fix"] is None
    assert sarvam["help"] == health.CONNECT_HELP

    assert _vision(entries) == []


# ── No database: the rules ──────────────────────────────────────────────────


def _wire(report):
    return [health.entry_wire(e) for e in report.entries]


class TestTheRules:
    def test_the_production_state_gives_the_three_entries(self):
        report = health.assess(PROD_CHAINS, PROD_CAPS, frozenset(PROD_KEYS))
        _assert_production_entries(_wire(report))
        (line,) = report.chains
        assert line.tier == "tier-stt"
        assert line.window_blocked is True
        assert line.reason == "3 entries on Speech to text cannot work"
        # No step works yet, so H5 has no reorder to offer.
        assert line.fix_chain is None

    def test_the_fix_composes_h2_h4_then_the_verb_when_the_key_is_exact(self):
        """With a platform key under `sarvam`, the alias entry gets the whole fix."""
        keys = frozenset({*PROD_KEYS, "sarvam"})
        report = health.assess(PROD_CHAINS, PROD_CAPS, keys)
        sarvam = next(e for e in report.entries if e.model == SARVAM_ALIAS)
        assert sarvam.rules[0] == "H2"
        assert sarvam.kind == "H2"
        assert sarvam.fix is not None
        assert (sarvam.fix.model, sarvam.fix.invocation) == (
            "sarvam/saaras:v4/translit", "native_sarvam")

    def test_sarvam_chat_gets_no_h3(self):
        """No native verb serves chat, so `acompletion` is right for it."""
        report = health.assess(
            {("tier-fast", "chat"): ["sarvam/sarvam-105b"]},
            {("sarvam/sarvam-105b", "chat"): "acompletion"},
            frozenset({"sarvam"}),
        )
        assert report.entries == ()
        assert report.chains == ()

    def test_a_sarvam_id_with_no_mode_works(self):
        """The handler adds `translit`, so the id alone is not an entry."""
        report = health.assess(
            {("tier-stt", "transcribe"): ["sarvam/saaras:v4"]},
            {("sarvam/saaras:v4", "transcribe"): "native_sarvam"},
            frozenset({"sarvam"}),
        )
        assert report.entries == ()

    def test_a_bound_model_with_no_key_is_h8(self):
        report = health.assess(
            {("tier-stt", "transcribe"): ["groq/whisper-large-v3-turbo"]},
            {("groq/whisper-large-v3-turbo", "transcribe"): "atranscription"},
            frozenset({"anthropic"}),
        )
        (entry,) = report.entries
        assert entry.kind == "H8"
        assert entry.reason == "No key for 'groq'"
        assert entry.fix is None
        assert entry.help == health.CONNECT_HELP

    def test_a_key_under_an_alias_is_h6_and_never_a_key(self):
        report = health.assess(
            {("tier-vision", "vision"): ["gemini/gemini-3.6-flash"]},
            {},
            frozenset({"google"}),
        )
        (entry,) = report.entries
        assert entry.kind == "H6"
        assert entry.reason == "The key is saved as 'google'"
        assert entry.fix is None

    def test_no_fix_names_a_provider_with_no_key(self):
        """The exact-key rule: `assemblyai/best` never gets a Groq fix."""
        report = health.assess(
            {("tier-stt", "transcribe"): [BEST]},
            {(BEST, "transcribe"): "atranscription"},
            frozenset({"assemblyai", "groq"}),
        )
        (entry,) = report.entries
        assert entry.fix is not None
        assert entry.fix.model == U35
        report = health.assess(
            {("tier-stt", "transcribe"): [BEST]},
            {(BEST, "transcribe"): "atranscription"},
            frozenset({"groq"}),
        )
        (entry,) = report.entries
        assert entry.fix is None

    def test_a_chat_step_with_no_capability_row_works(self):
        """The chat door calls `acompletion` with no row, so no entry."""
        report = health.assess(
            {("tier-fast", "chat"): ["deepseek/deepseek-chat"]}, {}, frozenset({"deepseek"})
        )
        assert report.entries == ()

    def test_a_transcribe_step_with_no_capability_row_is_h1(self):
        report = health.assess(
            {("tier-stt", "transcribe"): ["groq/whisper-large-v3-turbo"]}, {},
            frozenset({"groq"}),
        )
        (entry,) = report.entries
        assert entry.kind == "H1"
        assert entry.fix is not None
        assert (entry.fix.model, entry.fix.invocation) == (
            "groq/whisper-large-v3-turbo", "atranscription")

    def test_h5_offers_the_working_steps_when_the_window_is_dead(self):
        dead = [f"assemblyai/dead-{n}" for n in range(3)]
        chain = [*dead, "groq/whisper-large-v3-turbo"]
        caps = {(m, "transcribe"): "atranscription" for m in chain}
        # Keyed and capable, so the door counts all three dead steps first.
        keys = frozenset({"groq", "assemblyai"})
        report = health.assess({("tier-stt", "transcribe"): chain}, caps, keys)
        (line,) = report.chains
        assert line.window_blocked is True
        assert line.fix_chain == ("groq/whisper-large-v3-turbo",)
        # A step the door drops for no key takes no place in the window.
        report = health.assess(
            {("tier-stt", "transcribe"): chain}, caps, frozenset({"groq"})
        )
        (line,) = report.chains
        assert line.window_blocked is False

    def test_the_working_step_inside_the_window_is_not_blocked(self):
        chain = [NANO, "groq/whisper-large-v3-turbo"]
        caps = {(m, "transcribe"): "atranscription" for m in chain}
        report = health.assess(
            {("tier-stt", "transcribe"): chain}, caps, frozenset({"groq", "assemblyai"})
        )
        (line,) = report.chains
        assert line.window_blocked is False
        assert line.reason == "1 entry on Speech to text cannot work"


class TestThePlan:
    def _report(self, keys=PROD_KEYS):
        return health.assess(PROD_CHAINS, PROD_CAPS, frozenset(keys))

    def test_fix_all_writes_the_working_step_first(self):
        plan = health.plan_fix(
            self._report(), tier="tier-stt", task="transcribe",
            models=PROD_CHAINS[("tier-stt", "transcribe")], picked=[NANO, BEST],
        )
        assert plan.chain == (U35, SARVAM_ALIAS)
        assert plan.declares == ((U35, "native_assemblyai"),)
        assert plan.removed == (BEST,)

    def test_the_chain_fix_drops_every_dead_step(self):
        plan = health.plan_fix(
            self._report(), tier="tier-stt", task="transcribe",
            models=PROD_CHAINS[("tier-stt", "transcribe")], picked=[NANO, BEST, None],
        )
        assert plan.chain == (U35,)

    def test_an_entry_with_no_fix_is_a_400(self):
        with pytest.raises(health.FixRefused) as refused:
            health.plan_fix(
                self._report(), tier="tier-stt", task="transcribe",
                models=PROD_CHAINS[("tier-stt", "transcribe")], picked=[SARVAM_ALIAS],
            )
        assert refused.value.status == 400

    def test_a_model_that_is_no_step_is_a_409(self):
        with pytest.raises(health.FixRefused) as refused:
            health.plan_fix(
                self._report(), tier="tier-stt", task="transcribe",
                models=PROD_CHAINS[("tier-stt", "transcribe")], picked=["groq/x"],
            )
        assert refused.value.status == 409

    def test_the_chain_fix_with_nothing_working_is_a_400(self):
        with pytest.raises(health.FixRefused) as refused:
            health.plan_fix(
                self._report(), tier="tier-stt", task="transcribe",
                models=PROD_CHAINS[("tier-stt", "transcribe")], picked=[None],
            )
        assert refused.value.status == 400


def _calls_in(func: str) -> set[str]:
    tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func
    )
    return {
        c.func.id for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }


class TestTheSource:
    def test_every_chain_writer_takes_the_chain_lock(self):
        """A bind, an unbind and a fix take one lock, so none loses a write."""
        for route in ("bind_tier", "unbind_tier"):
            assert "_lock_chain" in _calls_in(route), route
        fix = _calls_in("_fix_one_tier")
        assert {"_lock_chain", "_write_capability", "_write_chain"} <= fix

    def test_the_two_routes_call_the_shared_writers(self):
        assert "_write_capability" in _calls_in("declare_capability")
        assert "_write_chain" in _calls_in("bind_tier")

    def test_the_fix_maps_the_three_sqlstates(self):
        from customer_console import main

        assert frozenset({"40P01", "40001", "55P03"}) == main._FIX_RETRY_SQLSTATES

    def test_the_fix_is_gated_as_a_bind(self):
        from customer_console.operator_roles import MATRIX, VIEWER

        assert MATRIX[("POST", "/catalog/health/fix")] == MATRIX[("POST", "/catalog/bindings")]
        assert MATRIX[("GET", "/catalog/health")].min_role == VIEWER
        assert MATRIX[("GET", "/catalog/providers")].min_role == VIEWER


def test_this_suite_is_named_in_the_ci_skip_guard():
    """A new R8 suite that CI does not name still SKIPS silently and reports
    green. This reads the hand-lists and fails if an entry is gone."""
    for flow in ("pr-check.yml", "deploy.yml"):
        ci = (ROOT / ".github/workflows" / flow).read_text(encoding="utf-8")
        assert "tests/unit/test_catalog_health_r8.py" in ci, flow


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


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


class _Production:
    """The production state of 2026-11-08, seeded, and taken back out after.

    Rows are inserted directly. The declare route refuses the production
    pairs today (that is the point of the fix), so it cannot seed them.

    ⚠️ Other live PLATFORM keys are revoked for the test and restored after,
    so the keys in force are exactly the production five.
    """

    def __init__(self, db) -> None:
        self.db = db
        self.since = datetime.now(UTC) - timedelta(seconds=2)
        self.revoked: list[str] = []
        self.caps: dict[tuple[str, str], tuple[str, bool] | None] = {}
        self.org_id: str | None = None

    def seed(self) -> None:
        with self.db.begin() as c:
            self.revoked = [str(r[0]) for r in c.execute(text(
                "UPDATE provider_credential SET revoked_at = now() "
                "WHERE organization_id IS NULL AND revoked_at IS NULL RETURNING id"))]
            for provider in PROD_KEYS:
                c.execute(text(
                    "INSERT INTO provider_credential (provider, secret_enc, label) "
                    "VALUES (:p, :s, :l)"),
                    {"p": provider, "s": router_mod.encrypt_secret(_secret(provider)),
                     "l": _LABEL})
            slug = f"ms1-{uuid.uuid4().hex[:8]}"
            self.org_id = str(c.execute(text(
                "INSERT INTO organization (slug, name) VALUES (:s, 'MS1') RETURNING id"),
                {"s": slug}).scalar_one())
            # BYOK: a customer's own Sarvam key. It must not count.
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc, label, organization_id) "
                "VALUES ('sarvam', :s, :l, CAST(:o AS uuid))"),
                {"s": router_mod.encrypt_secret(BYOK_SECRET), "l": _LABEL, "o": self.org_id})

            for pair in [*PROD_CAPS, (U35, "transcribe")]:
                row = c.execute(text(
                    "SELECT invocation, streams FROM model_capability "
                    "WHERE model = :m AND task = :t"), {"m": pair[0], "t": pair[1]}).first()
                self.caps[pair] = None if row is None else (row[0], bool(row[1]))
            c.execute(text(
                "DELETE FROM model_capability WHERE model = :m AND task = 'transcribe'"),
                {"m": U35})
            for (model, task), verb in PROD_CAPS.items():
                c.execute(text(
                    "INSERT INTO model_capability (model, task, invocation, streams) "
                    "VALUES (:m, :t, :v, FALSE) ON CONFLICT (model, task) DO UPDATE "
                    "SET invocation = EXCLUDED.invocation, streams = FALSE"),
                    {"m": model, "t": task, "v": verb})
            for (tier, task), models in PROD_CHAINS.items():
                for rank, model in enumerate(models, start=1):
                    c.execute(text(
                        "INSERT INTO tier_binding (tier, task, model, rank, effective_from) "
                        "VALUES (:tier, :task, :m, :r, :f)"),
                        {"tier": tier, "task": task, "m": model, "r": rank, "f": self.since})

    def clean(self) -> None:
        with self.db.begin() as c:
            for tier, task in PROD_CHAINS:
                c.execute(text(
                    "DELETE FROM tier_binding WHERE tier = :tier AND task = :task "
                    "AND effective_from >= :f"),
                    {"tier": tier, "task": task, "f": self.since})
            for (model, task), row in self.caps.items():
                c.execute(text(
                    "DELETE FROM model_capability WHERE model = :m AND task = :t"),
                    {"m": model, "t": task})
                if row is not None:
                    c.execute(text(
                        "INSERT INTO model_capability (model, task, invocation, streams) "
                        "VALUES (:m, :t, :v, :s)"),
                        {"m": model, "t": task, "v": row[0], "s": row[1]})
            c.execute(text("DELETE FROM provider_credential WHERE label = :l"), {"l": _LABEL})
            if self.org_id is not None:
                c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                          {"o": self.org_id})
            for cred in self.revoked:
                c.execute(text(
                    "UPDATE provider_credential SET revoked_at = NULL WHERE id = CAST(:i AS uuid)"),
                    {"i": cred})


@pytest.fixture
def prod(db):
    state = _Production(db)
    state.seed()
    yield state
    state.clean()


def _operator(db, role: str) -> tuple[dict[str, str], str]:
    """An operator holding *role*, with a live session."""
    from customer_console import operator_sessions, store

    email = f"ms1-{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
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


def _all_secrets() -> list[str]:
    return [*(_secret(p) for p in PROD_KEYS), BYOK_SECRET]


def _assert_no_secret(blob: str) -> None:
    for secret in _all_secrets():
        assert secret not in blob


@_DB
class TestTheProductionState:
    def test_health_gives_the_three_speech_entries_and_no_vision_entry(self, client, prod):
        headers, _ = _operator(prod.db, "viewer")
        r = client.get("/catalog/health", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        _assert_production_entries(body["entries"])
        lines = [c for c in body["chains"] if c["tier"] == "tier-stt"]
        assert lines == [{
            "kind": "H5", "tier": "tier-stt", "task": "transcribe",
            "job": "Speech to text", "entries": 3, "window_blocked": True,
            "reason": "3 entries on Speech to text cannot work", "fix": None,
        }]
        assert not [c for c in body["chains"] if c["tier"] == "tier-vision"]
        _assert_no_secret(r.text)

    def test_providers_count_the_exact_platform_key_only(self, client, prod):
        headers, _ = _operator(prod.db, "viewer")
        r = client.get("/catalog/providers", headers=headers)
        assert r.status_code == 200, r.text
        providers = {p["id"]: p for p in r.json()["providers"]}
        assert providers["assemblyai"]["connected"] is True
        assert providers["assemblyai"]["credential_label"] == _LABEL
        assert providers["anthropic"]["connected"] is True
        # The platform key sits under `sarvam.ai`, and the BYOK key is a
        # customer's. Neither connects Sarvam.
        assert providers["sarvam"]["connected"] is False
        assert providers["sarvam"]["credential_label"] is None
        assert providers["groq"]["connected"] is False
        _assert_no_secret(r.text)

    def test_the_byok_key_gives_the_sarvam_entry_no_fix(self, client, prod):
        r = client.get("/catalog/health", headers=OP)
        sarvam = next(e for e in r.json()["entries"] if e["model"] == SARVAM_ALIAS)
        assert sarvam["fix"] is None
        assert sarvam["kind"] == "H6"


@_DB
class TestTheFix:
    def test_fix_all_on_speech_to_text(self, client, prod, caplog):
        headers, email = _operator(prod.db, "admin")
        caplog.set_level(logging.DEBUG)
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-stt", "task": "transcribe", "model": NANO},
            {"tier": "tier-stt", "task": "transcribe", "model": BEST},
        ]})
        assert r.status_code == 200, r.text
        (fixed,) = r.json()["fixed"]
        assert fixed["was"] == [SARVAM_ALIAS, NANO, BEST]
        assert fixed["chain"] == [U35, SARVAM_ALIAS]
        assert fixed["replaced"] == [{"from": NANO, "to": U35}]
        assert fixed["removed"] == [BEST]
        assert _chain(prod.db, "tier-stt", "transcribe") == [U35, SARVAM_ALIAS]

        with prod.db.begin() as c:
            # A new declare for the fixed id. The old declare stays as it was.
            assert c.execute(text(
                "SELECT invocation FROM model_capability WHERE model = :m AND task = 'transcribe'"),
                {"m": U35}).scalar_one() == "native_assemblyai"
            assert c.execute(text(
                "SELECT invocation FROM model_capability WHERE model = :m AND task = 'transcribe'"),
                {"m": NANO}).scalar_one() == "atranscription"
            # Insert only: the old chain row is still there, under the new one.
            sets = c.execute(text(
                "SELECT count(DISTINCT effective_from) FROM tier_binding "
                "WHERE tier = 'tier-stt' AND task = 'transcribe' AND effective_from >= :f"),
                {"f": prod.since}).scalar_one()
            assert sets == 2
            audits = c.execute(text(
                "SELECT action, detail FROM control_audit WHERE actor = :a"),
                {"a": email}).all()
        # One transaction, so both rows carry one `created_at`. Read by action.
        assert sorted(a[0] for a in audits) == ["catalog.binding", "catalog.capability"]
        by_action = {a[0]: a[1] for a in audits}
        assert by_action["catalog.capability"] == {
            "model": U35, "task": "transcribe", "invocation": "native_assemblyai"}
        binding = by_action["catalog.binding"]
        assert binding["tier"] == "tier-stt"
        assert binding["task"] == "transcribe"
        assert binding["chain"] == [U35, SARVAM_ALIAS]
        assert binding["via"] == "health_fix"
        assert binding["was"] == [SARVAM_ALIAS, NANO, BEST]
        _assert_no_secret(r.text)
        _assert_no_secret(json.dumps([a[1] for a in audits]))
        _assert_no_secret(caplog.text)

        # Health again: only the Sarvam entry is left, and the window works.
        after = client.get("/catalog/health", headers=headers).json()
        stt = _stt(after["entries"])
        assert [e["model"] for e in stt] == [SARVAM_ALIAS]
        line = next(c for c in after["chains"] if c["tier"] == "tier-stt")
        assert line["window_blocked"] is False
        assert line["reason"] == "1 entry on Speech to text cannot work"

    def test_an_entry_with_no_fix_is_a_400_and_writes_nothing(self, client, prod):
        headers, _ = _operator(prod.db, "admin")
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-stt", "task": "transcribe", "model": SARVAM_ALIAS}]})
        assert r.status_code == 400, r.text
        assert "The key is saved as 'sarvam.ai'" in r.json()["detail"]
        assert _chain(prod.db, "tier-stt", "transcribe") == [SARVAM_ALIAS, NANO, BEST]

    def test_a_stale_entry_is_a_409(self, client, prod):
        headers, _ = _operator(prod.db, "admin")
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-stt", "task": "transcribe", "model": "groq/whisper-large-v3-turbo"}]})
        assert r.status_code == 409, r.text

    def test_a_working_step_is_a_409(self, client, prod):
        headers, _ = _operator(prod.db, "admin")
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-vision", "task": "vision", "model": HAIKU}]})
        assert r.status_code == 409, r.text

    @pytest.mark.parametrize("body", [
        {"entries": []},
        {"entries": [{"tier": "", "task": "transcribe", "model": NANO}]},
        {"entries": [{"tier": "tier-stt", "task": "transcribe", "model": NANO, "x": 1}]},
        {},
    ])
    def test_bad_input_is_a_422(self, client, prod, body):
        headers, _ = _operator(prod.db, "admin")
        r = client.post("/catalog/health/fix", headers=headers, json=body)
        assert r.status_code == 422, r.text

    def test_an_unknown_task_is_a_400(self, client, prod):
        headers, _ = _operator(prod.db, "admin")
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-stt", "task": "no-such-task", "model": NANO}]})
        assert r.status_code == 400, r.text

    def test_a_lock_wait_is_a_409_and_writes_nothing(self, client, prod, monkeypatch):
        """The fix takes the SAME lock a second writer holds, then gives up."""
        from customer_console import main

        monkeypatch.setattr(main, "_FIX_LOCK_TIMEOUT", "200ms")
        headers, _ = _operator(prod.db, "admin")
        holder = prod.db.connect()
        tx = holder.begin()
        try:
            holder.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": main._chain_lock_key("tier-stt", "transcribe")},
            )
            r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
                {"tier": "tier-stt", "task": "transcribe", "model": NANO}]})
        finally:
            tx.rollback()
            holder.close()
        assert r.status_code == 409, r.text
        assert "Try again" in r.json()["detail"]
        assert _chain(prod.db, "tier-stt", "transcribe") == [SARVAM_ALIAS, NANO, BEST]

    def test_the_bind_route_still_writes_a_whole_chain(self, client, prod):
        """The bind moved into `_write_chain`. One timestamp, the new set wins."""
        with prod.db.begin() as c:
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation, streams) "
                "VALUES (:m, 'transcribe', 'native_assemblyai', FALSE) "
                "ON CONFLICT (model, task) DO NOTHING"), {"m": U35})
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": "tier-stt", "task": "transcribe", "models": [U35, NANO]})
        assert r.status_code == 200, r.text
        assert _chain(prod.db, "tier-stt", "transcribe") == [U35, NANO]


@_DB
class TestTheGate:
    @pytest.mark.parametrize("role", ["viewer", "editor"])
    def test_only_an_admin_reaches_the_fix(self, client, prod, role):
        headers, _ = _operator(prod.db, role)
        r = client.post("/catalog/health/fix", headers=headers, json={"entries": [
            {"tier": "tier-stt", "task": "transcribe", "model": NANO}]})
        assert r.status_code == 403
        assert _chain(prod.db, "tier-stt", "transcribe") == [SARVAM_ALIAS, NANO, BEST]

    def test_with_elevation_on_an_admin_needs_a_window(self, client, prod, monkeypatch):
        from customer_console.operator_elevation import ELEVATION_FLAG

        monkeypatch.setenv(ELEVATION_FLAG, "1")
        headers, _ = _operator(prod.db, "admin")
        body = {"entries": [{"tier": "tier-stt", "task": "transcribe", "model": NANO}]}
        assert client.post("/catalog/health/fix", headers=headers, json=body).status_code == 403
        opened = client.post("/operators/elevate", headers=headers,
                             json={"reason": "ms1 health fix fence"})
        assert opened.status_code == 200, opened.text
        r = client.post("/catalog/health/fix", headers=headers, json=body)
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("path", ["/catalog/health", "/catalog/providers"])
    def test_a_viewer_reads_both_lists(self, client, prod, path):
        headers, _ = _operator(prod.db, "viewer")
        assert client.get(path, headers=headers).status_code == 200
