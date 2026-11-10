"""One wedged handler must not be able to take the database down.

The 2026-08-06 outage, and the three independent bounds that now stop it
recurring. Worth stating the chain once, because every assertion below is a link
in it and each looks arbitrary on its own:

1. An LLM completion hung. ``acompletion`` was awaited with no wall-clock bound.
2. The caller held a SQLAlchemy ``AsyncSession`` across that await. A session
   opens a transaction on first ``execute()`` and holds it until
   commit/rollback/close, so the hung call parked a Postgres transaction
   `idle in transaction` — holding ACCESS SHARE on ``email_assistant_settings``
   — for 14h44m.
3. ``apply_migrations.sh`` asked for ACCESS EXCLUSIVE on that table to replay an
   idempotent ``ALTER TABLE``, and waited. Postgres's lock queue is FIFO, so
   every later reader queued behind the *waiting* ALTER — including the one the
   send path makes to read the account's signature. Sending mail stopped, and
   the connection pool drained behind the blocked readers.

Any ONE of these bounds breaks the chain. All three are here because the chain
had no bound at all, and the cheapest way for it to come back is for someone to
restore one link while the other two look fine.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ── Link 1: the LLM call has a wall-clock ceiling ────────────────────────────

def test_request_timeout_default_is_positive_and_finite():
    from acb_llm.client import _request_timeout_secs

    ceiling = _request_timeout_secs()
    assert 0 < ceiling < 600, (
        "an absent or enormous ceiling re-opens the unbounded await that "
        "parked a transaction for 14h44m"
    )


def test_request_timeout_honours_env_and_ignores_junk(monkeypatch):
    from acb_llm.client import _DEFAULT_REQUEST_TIMEOUT_SECS, _request_timeout_secs

    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECS", "12.5")
    assert _request_timeout_secs() == 12.5

    # A typo'd or negative value must fall back, never disable the bound.
    for junk in ("banana", "0", "-5", ""):
        monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECS", junk)
        assert _request_timeout_secs() == _DEFAULT_REQUEST_TIMEOUT_SECS


def _stub_llm(monkeypatch, acompletion):
    """Neutralise everything `complete` touches except the call under test."""
    from acb_llm import client as m

    async def _no_keys() -> None:
        return None

    monkeypatch.setattr(m, "_ensure_keys_loaded", _no_keys)
    monkeypatch.setattr(m, "ensure_model_registered", lambda _model: None)
    monkeypatch.setattr(
        m, "apply_prompt_caching",
        lambda *, model, messages, tools, cache_key, extra: (messages, tools, extra),
    )
    monkeypatch.setattr(m, "_emit_usage", lambda *a, **k: None)
    monkeypatch.setattr(m, "acompletion", acompletion)
    # Skip the 2s/4s inter-attempt backoff so this stays a unit test. The
    # ceiling itself is NOT skipped — that is what is under test.

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(m.asyncio, "sleep", _no_sleep)
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECS", "0.05")


async def test_a_hanging_completion_raises_instead_of_hanging(monkeypatch):
    """THE regression. A provider that never answers must not pin the caller.

    Before the fix this awaited forever, and because callers hold a DB session
    across it, forever meant a Postgres transaction held open for as long as the
    process lived.
    """
    from acb_llm.client import LLMTier, complete

    never = asyncio.Event()
    attempts = {"n": 0}

    async def _hang(**_kw):
        attempts["n"] += 1
        await never.wait()  # a provider that accepted the request and went quiet

    _stub_llm(monkeypatch, _hang)  # ceiling = 0.05s

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            complete(tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "x"}]),
            timeout=10,
        )

    # NOT vacuous. The outer wait_for would ALSO raise TimeoutError if the inner
    # ceiling were missing, so `pytest.raises` alone would pass on the broken
    # code. The count is what distinguishes the fix: a caller that never
    # bounded itself would only ever have entered `acompletion` once, and the
    # outer wait would end it there. A clock bound of 2 s on three attempts of
    # 0.05 s said the same thing, and it measured the runner too.
    assert attempts["n"] == 3, "each attempt must be bounded, not just the first"


async def test_a_timeout_is_retried_not_re_raised(monkeypatch):
    """``except TimeoutError`` must sit ABOVE the generic handler.

    ``asyncio.wait_for``'s TimeoutError stringifies to '', so the generic
    handler's ``any(token in str(exc).lower() ...)`` test reads it as
    NON-transient and re-raises. Ordering the handlers wrongly would turn a
    single slow provider response into a hard failure on attempt 1 — converting
    the fix into a new, quieter outage.
    """
    from acb_llm.client import LLMTier, complete

    calls = {"n": 0}
    never = asyncio.Event()

    async def _hang_twice_then_answer(**_kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            await never.wait()
        return {"choices": [{"message": {"content": "recovered"}}]}

    _stub_llm(monkeypatch, _hang_twice_then_answer)

    # The outer bound is a CI guard, not the assertion. Without the ceiling this
    # test would otherwise block forever on the first hang, and a suite that
    # hangs is strictly worse feedback than one that fails — that is the whole
    # lesson of the incident, applied to the test that pins it.
    out = await asyncio.wait_for(
        complete(tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "x"}]),
        timeout=10,
    )
    assert out == "recovered"
    assert calls["n"] == 3, "both timeouts should have been retried, not raised"


# ── Link 2: a session cannot sit `idle in transaction` forever ───────────────

def test_engine_connect_args_bound_both_getting_in_and_staying_in():
    from gateway.db import engine_connect_args

    args = engine_connect_args()
    assert args["timeout"] > 0, "connect phase must stay bounded"

    ms = args["server_settings"]["idle_in_transaction_session_timeout"]
    # asyncpg passes server_settings verbatim in the startup packet, so a
    # non-string here fails at connect time, in production, not here.
    assert isinstance(ms, str), "asyncpg requires server_settings values as str"
    assert int(ms) > 0


def test_idle_ceiling_clears_the_llm_worst_case():
    """The coupling that a future edit will otherwise silently break.

    A rules run legitimately awaits a completion with a session open, and
    ``complete`` retries 3x with 2s+4s of backoff. If the DB's idle ceiling ever
    drops below that, Postgres starts killing HEALTHY transactions mid-retry —
    which reads as random, unattributable failures under load.
    """
    from acb_llm.client import _request_timeout_secs
    from gateway.db import engine_connect_args

    llm_worst_case = 3 * _request_timeout_secs() + 6
    idle_ceiling = int(
        engine_connect_args()["server_settings"][
            "idle_in_transaction_session_timeout"
        ]
    ) / 1000
    assert idle_ceiling > llm_worst_case, (
        f"idle_in_transaction_session_timeout ({idle_ceiling}s) must exceed the "
        f"LLM worst case ({llm_worst_case}s). Raise one and you must raise the "
        f"other."
    )


def test_every_gateway_engine_carries_the_bounds():
    """A new app package must not be able to open engine number eight without it.

    Per-site opt-in is how the email engine ended up as the one that drained;
    this is the guard that makes forgetting visible.
    """
    roots = [REPO / "apps/services/gateway/gateway"]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            src = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"create_async_engine\(", src):
                tail = src[match.start():match.start() + 900]
                if "engine_connect_args()" not in tail:
                    line = src[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert not offenders, (
        "these engines do not bound idle-in-transaction sessions: "
        + ", ".join(offenders)
    )


# ── Link 3: a migration never WAITS for a lock ───────────────────────────────

def _migration_runner() -> str:
    return (REPO / "scripts/apply_migrations.sh").read_text(encoding="utf-8")


def test_migrations_set_a_lock_timeout():
    src = _migration_runner()
    assert "SET lock_timeout" in src, (
        "without lock_timeout an ALTER TABLE queues for ACCESS EXCLUSIVE and, "
        "because the lock queue is FIFO, freezes the table for every later "
        "reader — which is what took sending mail down"
    )
    assert "MIGRATION_LOCK_TIMEOUT" in src


def test_the_lock_timeout_value_is_QUOTED():
    """The bug this file previously shipped, now pinned.

    `SET lock_timeout = 5s;` is not valid SQL — Postgres answers "trailing junk
    after numeric literal". Under ON_ERROR_STOP=1 that failed the FIRST
    migration, so the guard meant to stop a stalled session blocking deploys
    blocked every deploy itself. `'5s'` and `'5000'` are both accepted.

    The old assertion (`"SET lock_timeout" in src`) passed on the broken code,
    which is exactly why presence is not a substitute for validity.
    """
    # Comment lines are excluded deliberately: the runner QUOTES the broken form
    # in its own explanation of this bug, and a test that cannot tell an example
    # from an instruction would fail on the very documentation that prevents the
    # next occurrence.
    code = "\n".join(
        line for line in _migration_runner().splitlines()
        if not line.lstrip().startswith("#")
    )
    stmts = re.findall(r"SET lock_timeout\s*=\s*[^;\n]+", code)
    assert stmts, "no lock_timeout statement found in the runner's actual code"
    for stmt in stmts:
        value = stmt.split("=", 1)[1].strip()
        assert value.startswith("'") and value.rstrip(";").endswith("'"), (
            f"unquoted lock_timeout value in {stmt!r} — Postgres rejects a bare "
            f"unit like 5s, and ON_ERROR_STOP=1 turns that into a failed deploy"
        )


def test_a_bad_prelude_degrades_instead_of_bricking_the_deploy():
    """A safety feature must not be able to brick the thing it protects.

    If the prelude will not parse, the runner has to warn and apply migrations
    WITHOUT it. Failing closed here means an un-deployable box, which is
    strictly worse than the stall the guard exists to prevent.
    """
    src = _migration_runner()
    assert "LOCK_PRELUDE" in src, "the prelude must be a variable that can be emptied"
    assert re.search(r'LOCK_PRELUDE=""', src), (
        "no fallback path — a prelude Postgres rejects would fail every migration"
    )
    assert re.search(r'\[ -n "\$LOCK_PRELUDE" \]', src), (
        "the emit site must skip an emptied prelude rather than print nothing "
        "meaningful into the SQL stream"
    )


def test_the_unbounded_psql_invocation_is_gone():
    """Pins the SHAPE, not just the presence of a setting.

    Re-adding a bare `psql ... < "$f"` alongside the bounded one would leave the
    hazard in place while every other assertion here still passed.
    """
    src = _migration_runner()
    assert not re.search(r'psql[^\n|]*<\s*"\$f"', src), (
        "a migration is being piped to psql without the lock_timeout prelude"
    )


def test_only_lock_timeouts_are_retried():
    """A retry loop that swallows real SQL errors would be worse than no loop."""
    src = _migration_runner()
    assert "MIGRATION_LOCK_RETRIES" in src
    assert re.search(r"grep -qi 'lock timeout'", src), (
        "the retry must be gated on the error actually being a lock timeout"
    )
