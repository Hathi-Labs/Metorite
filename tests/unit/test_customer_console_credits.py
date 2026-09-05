"""Credit rating, balance and the spend gate.

Spec: ``project-docs/specs/customer_console.md`` §3.4/§4.4 · D19.2 (the
credit unit) · D32.6 (rollover) · D32.8 (per-member caps).

Three failure modes drive these tests, all of them expensive in different ways:

  1. **Double-billing cached tokens** — charges the customer most for the part
     of the prompt that cost us least, i.e. the exact opposite of what prompt
     caching was sold to them as.
  2. **Billing an unpriced model as free** — looks like revenue working while
     the margin leaks, and nobody notices until the month closes.
  3. **A hard stop at exactly zero** — lands mid-workflow and costs more in
     support than the overdraft ever will.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal

import pytest
from customer_console.credits import (
    CREDIT_QUANTUM,
    OverdraftPolicy,
    RateCard,
    RunCeiling,
    TokenUsage,
    UnpricedModel,
    UsagePartitionError,
    balance_of,
    decide_member_cap,
    decide_run_ceiling,
    decide_spend,
    quantize_credits,
    rate_call,
)

#: ⚠️ `pricing_mode="priced"` is REQUIRED as of CP-10 slice 2 (D61, G-4).
#: Numbers alone no longer make a card billable, because a zero cannot carry
#: three meanings — not-yet-priced, absorbed into the seat price (D19.2), and
#: deliberately free are three different states with one number.
#: ⚠️ Restated at the per-MILLION scale (migration 024). These are the SAME
#: prices as before — 2.0 per 1k IS 2000 per 1M — so every expected credit
#: figure in this file is unchanged. If one of them moves, the conversion is
#: wrong, not the test.
CARD = RateCard(
    model="deepseek/deepseek-v4-pro",
    input_per_1m=Decimal("2000"),
    output_per_1m=Decimal("6000"),
    cached_input_per_1m=Decimal("500"),
    pricing_mode="priced",
)

#: A minute of audio, priced per minute. `transcribe` could not be priced at
#: all before this slice, and `tier-stt` ships in the production seed.
STT_CARD = RateCard(
    model="groq/whisper-large-v3-turbo",
    input_per_1m=Decimal(0),
    output_per_1m=Decimal(0),
    task="transcribe",
    unit="minutes",
    credits_per_unit=Decimal("0.4"),
    pricing_mode="priced",
)


class TestRating:
    def test_a_plain_call(self):
        # 1000 fresh input @2 + 500 output @6 = 2 + 3 = 5 credits
        cost = rate_call(CARD, TokenUsage(prompt_tokens=1000, completion_tokens=500))
        assert cost == Decimal("5.0")

    def test_cached_tokens_are_a_SUBSET_of_prompt_tokens(self):
        # The trap. Providers report cached tokens as part of prompt_tokens, so
        # billing both at full rate charges twice for the same text.
        #
        # 1000 prompt of which 800 cached →
        #   200 fresh @2/1k = 0.4  +  800 cached @0.5/1k = 0.4  +  0 output
        cost = rate_call(CARD, TokenUsage(prompt_tokens=1000, cached_tokens=800))
        assert cost == Decimal("0.8")

        # And it must be strictly cheaper than the same call with no cache hit,
        # or the discount we advertise is not real.
        uncached = rate_call(CARD, TokenUsage(prompt_tokens=1000))
        assert cost < uncached

    def test_cached_exceeding_prompt_REFUSES(self):
        """⚠️ This REPLACES `test_cached_exceeding_prompt_never_goes_negative`.

        That test asserted the CLAMP — `max(0, prompt - cached)` — and it was
        right that a negative fresh count would credit the customer for using
        more cache. It was wrong about the remedy. Clamping answers a plausible
        number where the honest answer is "these counts cannot both be true",
        and `credit_pricing.md` §3 measured what that cost: a sibling-convention
        report undercharged by 27 % with no error and no log line.

        Refusing keeps the old guarantee — nothing goes negative — and adds the
        one the clamp threw away, which is that somebody finds out.
        """
        with pytest.raises(UsagePartitionError):
            rate_call(CARD, TokenUsage(prompt_tokens=100, cached_tokens=500))

    def test_an_unpriced_model_raises_rather_than_billing_zero(self):
        # 002_seed_catalog.sql seeds every model at zero on purpose, so this
        # fires until CP-6 sets real prices against measured burn.
        unpriced = RateCard("new/model", Decimal(0), Decimal(0), Decimal(0))
        assert unpriced.is_priced is False
        with pytest.raises(UnpricedModel):
            rate_call(unpriced, TokenUsage(prompt_tokens=1_000_000))

    def test_numbers_alone_no_longer_make_a_card_billable(self):
        """⚠️ This REPLACES `test_a_card_with_any_nonzero_rate_is_priced`.

        That test asserted the old rule — any non-zero rate meant priced — and
        D61's G-4 retires it. The mode is the authority now. A card carrying
        real numbers that nobody marked `priced` **fails closed**, which is the
        safe direction: it refuses to bill rather than billing a rate somebody
        was still drafting.
        """
        drafted = RateCard("m", Decimal(0), Decimal("0.1"))
        assert drafted.pricing_mode == "unpriced"
        assert drafted.is_priced is False
        with pytest.raises(UnpricedModel):
            rate_call(drafted, TokenUsage(prompt_tokens=1000))

        assert RateCard("m", Decimal(0), Decimal("0.1"), pricing_mode="priced").is_priced is True

    def test_absorbed_is_free_and_is_NOT_an_error(self):
        """D19.2 absorbs embeddings into the seat price. That is not a mistake.

        An absorbed task that raised `UnpricedModel` would be indistinguishable
        from a misconfigured one, and somebody would "fix" it by inventing a
        price the customer never agreed to.
        """
        absorbed = RateCard("m", Decimal(0), Decimal(0), task="embed", pricing_mode="absorbed")
        assert absorbed.is_priced is False
        assert absorbed.is_absorbed is True
        assert rate_call(absorbed, TokenUsage(prompt_tokens=1_000_000)) == 0


class TestUnitsOtherThanTokens:
    """🔴 Three of six tasks could not be priced at all before this slice.

    `transcribe` is sold per minute of audio (D19.2 says so in terms), `speak`
    per character and `image` per image. None of them divides a token count by
    1000, and `rate_call` knew no other unit.
    """

    def test_a_transcription_is_priced_per_minute(self):
        cost = rate_call(STT_CARD, TokenUsage(), quantity=Decimal("3.5"))
        assert cost == Decimal("1.4")

    def test_a_per_unit_card_with_no_quantity_REFUSES(self):
        """⚠️ It must not fall back to the token rates.

        A minute of audio rated per 1k tokens produces a number, and a
        plausible one, and a wrong one. Refusing is visible; a plausible wrong
        number is not.
        """
        with pytest.raises(UnpricedModel) as exc:
            rate_call(STT_CARD, TokenUsage(prompt_tokens=5000))
        assert "minutes" in str(exc.value)

    def test_a_token_card_ignores_quantity(self):
        # Its quantity IS the usage counters. A stray quantity must not
        # double-count or override them.
        with_q = rate_call(
            CARD,
            TokenUsage(prompt_tokens=1000, completion_tokens=500),
            quantity=Decimal("999"),
        )
        assert with_q == Decimal("5.0")

    def test_the_unit_price_is_decimal_not_float(self):
        assert isinstance(rate_call(STT_CARD, TokenUsage(), quantity=Decimal(1)), Decimal)

    def test_money_is_decimal_not_float(self):
        # Small rates x large token counts, summed thousands of times a month,
        # is exactly where binary floating point drifts.
        assert isinstance(rate_call(CARD, TokenUsage(prompt_tokens=1)), Decimal)


class TestBalance:
    def test_balance_is_the_sum_of_the_ledger(self):
        assert balance_of([Decimal("1000"), Decimal("-5.5"), Decimal("-4.5")]) == Decimal("990")

    def test_an_empty_ledger_is_zero_not_an_error(self):
        assert balance_of([]) == Decimal(0)

    def test_credits_roll_over_because_a_balance_is_just_a_sum(self):
        # D32.6: rollover is the DEFAULT — there is no per-period reset to
        # implement, which is exactly why expiry would be the thing needing
        # machinery, not rollover.
        last_month = [Decimal("1000"), Decimal("-300")]
        this_month = [Decimal("-200")]
        assert balance_of(last_month + this_month) == Decimal("500")


class TestTheSpendGate:
    def test_allows_a_call_within_balance(self):
        assert decide_spend(Decimal("100"), Decimal("5")).allowed is True

    def test_soft_blocks_into_grace_rather_than_at_zero(self):
        # Balance 0, cost 10, grace 100 → allowed, and flagged as overdraft so
        # the UI warns before the wall instead of at it.
        d = decide_spend(Decimal("0"), Decimal("10"))
        assert d.allowed is True
        assert d.in_overdraft is True

    def test_refuses_402_past_the_grace_floor(self):
        d = decide_spend(Decimal("-95"), Decimal("10"))
        assert d.allowed is False
        # 402, not 409: this IS the "not paid for" axis.
        assert d.status == 402
        assert d.reason == "insufficient_credits"

    def test_the_floor_is_checked_after_the_call_not_before(self):
        # Otherwise one very large call vaults past the floor in a single step
        # and the grace is whatever that call happened to cost.
        policy = OverdraftPolicy(grace_credits=Decimal("10"))
        assert decide_spend(Decimal("0"), Decimal("11"), policy=policy).allowed is False
        assert decide_spend(Decimal("0"), Decimal("10"), policy=policy).allowed is True

    def test_trials_get_no_grace_by_default(self):
        # An unpaid account is where overdraft becomes unrecoverable cost.
        assert decide_spend(Decimal("0"), Decimal("1"), is_trial=True).allowed is False
        assert decide_spend(Decimal("1"), Decimal("1"), is_trial=True).allowed is True

    def test_trial_grace_is_available_when_deliberately_enabled(self):
        policy = OverdraftPolicy(grace_credits=Decimal("50"), grace_for_trial=True)
        assert (
            decide_spend(Decimal("0"), Decimal("10"), policy=policy, is_trial=True).allowed is True
        )


class TestMemberCaps:
    def test_no_cap_row_means_unlimited_within_the_org_pool(self):
        # Absence of a policy is not a policy of zero.
        assert decide_member_cap(Decimal("10000"), None) == ("allow", False)

    def test_degrade_is_the_default_at_exhaustion(self):
        # Dropping to tier-fast keeps the member working. A system exposing raw
        # model names could only stop them (D32.8).
        assert decide_member_cap(Decimal("100"), Decimal("100")) == ("degrade", True)

    def test_block_when_explicitly_configured(self):
        action, warn = decide_member_cap(Decimal("100"), Decimal("100"), on_exhaustion="block")
        assert (action, warn) == ("block", True)

    def test_warns_at_80_percent_without_restricting(self):
        assert decide_member_cap(Decimal("80"), Decimal("100")) == ("allow", True)
        assert decide_member_cap(Decimal("79"), Decimal("100")) == ("allow", False)

    def test_a_zero_or_negative_cap_is_treated_as_no_cap(self):
        # A cap of zero almost certainly means "not configured"; reading it as
        # "this member may never use AI" would silently disable people.
        assert decide_member_cap(Decimal("5"), Decimal("0")) == ("allow", False)


# ── CP-6: the overdraft at both edges of the SHIPPED value ──────────────────


class TestTheOverdraftIsANamedConfigValue:
    """Acceptance: *"the ~10 percent overdraft is a named config value with a
    test at both edges"*.

    The spec line says "~10 percent"; the shipped value is **absolute** and the
    docstring on :class:`OverdraftPolicy` argues why — a percentage of lifetime
    purchases silently grows the exposure on your largest accounts, which is
    the opposite of what a credit limit is for. So the edges tested here are
    the edges of the value actually in force, not of the sentence.
    """

    def test_the_named_value_is_100_absolute_credits(self):
        assert OverdraftPolicy().grace_credits == Decimal(100)
        assert OverdraftPolicy().grace_for_trial is False

    def test_the_last_call_INSIDE_the_grace_floor_is_allowed(self):
        # Balance -99.9999 spending one quantum lands exactly ON -100, which is
        # inside the floor: the comparison is `projected < -grace`.
        assert decide_spend(Decimal("-99.9999"), CREDIT_QUANTUM).allowed is True

    def test_the_first_call_PAST_the_grace_floor_is_refused_402(self):
        d = decide_spend(Decimal("-100"), CREDIT_QUANTUM)
        assert d.allowed is False
        assert d.status == 402
        assert d.reason == "insufficient_credits"

    def test_the_grace_is_absolute_not_a_percentage_of_what_was_bought(self):
        # The distinguishing case: an organization that has bought a great deal
        # over its life and is now at -150. Under a 10-percent-of-purchases
        # rule it would still be spending; under the shipped absolute rule it
        # stopped at -100.
        assert decide_spend(Decimal("-150"), CREDIT_QUANTUM).allowed is False

    def test_the_402_carries_a_top_up_payload_the_UI_can_render(self):
        d = decide_spend(Decimal("-120"), Decimal("5"))
        assert d.top_up is not None
        assert d.top_up["balance_credits"] == "-120"
        assert d.top_up["grace_credits"] == "100"
        # Enough to get back to zero — the number an admin can act on.
        assert d.top_up["credits_required"] == "125"
        assert d.top_up["is_trial"] is False

    def test_an_allowed_call_carries_no_top_up_payload(self):
        assert decide_spend(Decimal("1000"), Decimal("5")).top_up is None

    def test_a_trial_has_no_grace_at_either_edge(self):
        # Both edges again, for the state every new organization starts in.
        assert decide_spend(Decimal("0"), CREDIT_QUANTUM, is_trial=True).allowed is False
        assert decide_spend(CREDIT_QUANTUM, CREDIT_QUANTUM, is_trial=True).allowed is True


class TestTheCreditQuantum:
    def test_it_matches_what_the_ledger_column_can_store(self):
        # credit_ledger.delta and usage_event.billed_credits are NUMERIC(14,4).
        assert Decimal("0.0001") == CREDIT_QUANTUM

    def test_a_rated_cost_is_rounded_to_the_stored_precision(self):
        # Otherwise Postgres rounds it for us and the number we billed is not
        # the number we computed.
        assert quantize_credits(Decimal("1.234567")) == Decimal("1.2346")

    def test_a_sub_quantum_call_rounds_to_zero_and_so_writes_no_ledger_row(self):
        assert quantize_credits(Decimal("0.00004")) == Decimal(0)


# ── CP-6: the per-run circuit breaker ───────────────────────────────────────


class TestThePerRunCircuitBreaker:
    """§4.4: *"A per-run spend ceiling is not optional."*"""

    def test_the_ceiling_is_a_named_value(self):
        assert RunCeiling().max_credits == Decimal(500)

    def test_a_run_below_the_ceiling_proceeds(self):
        assert decide_run_ceiling(Decimal("499.9999")).allowed is True

    def test_a_run_AT_the_ceiling_is_stopped(self):
        d = decide_run_ceiling(Decimal("500"))
        assert d.allowed is False
        # 403, not 402: topping up does not fix a runaway loop, and a 402 would
        # render as "out of credits" to a customer who has plenty.
        assert d.status == 403
        assert d.reason == "run_ceiling_exceeded"

    def test_it_breaks_a_runaway_loop_rather_than_merely_reporting_it(self):
        # The shape of the thing it exists to stop: a tool loop that keeps
        # calling. Spend accumulates, and the loop TERMINATES rather than
        # running to its own retry limit.
        ceiling = RunCeiling(max_credits=Decimal("10"))
        spent, calls = Decimal(0), 0
        for _ in range(1000):
            if not decide_run_ceiling(spent, ceiling=ceiling).allowed:
                break
            spent += Decimal("1")
            calls += 1
        assert calls == 10
        assert spent == Decimal("10")

    def test_a_fresh_run_starts_from_zero(self):
        # The ceiling is per (organization, run): a second run is not punished
        # for the first one's spend. That is what makes it a tripwire on a loop
        # rather than a budget on the customer.
        assert decide_run_ceiling(Decimal(0)).allowed is True


# ── R7: the structural fence (CP-6 acceptance clause 1) ─────────────────────

#: ``<repo>/tests/unit/test_customer_console_credits.py`` -> ``<repo>``.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".codegraph",
        "dist",
        "build",
        "site-packages",
        "htmlcov",
        ".turbo",
        ".uv",
    }
)

_SCANNED = (".py", ".sql", ".ts", ".tsx")

#: An ``UPDATE`` and everything up to the end of its statement.
_UPDATE_BODY = re.compile(r"\bUPDATE\b([^;]{0,800})", re.IGNORECASE | re.DOTALL)

#: A column being ASSIGNED inside a SET clause. The assignment TARGET is what
#: matters, never the value: ``SET chat_model = 'tier-balanced'`` exists in
#: ``infra/postgres/42_email_model_roles.sql`` and is not a balance write, but a
#: naive search for "balance" near "UPDATE" flags it. That near-miss is why
#: this matches the identifier on the left of the ``=``.
_SET_TARGET = re.compile(r"(?:\bSET\b|,)\s*\"?([A-Za-z_][A-Za-z0-9_.]*)\"?\s*=", re.IGNORECASE)

#: A column DECLARATION whose name contains "balance" — the other half of the
#: same doctrine. §3.4's SQL sketch lists ``balance_after`` on
#: ``credit_ledger``; ``001_customer_console.sql`` omits it on purpose and its
#: COMMENT says why. A column that exists is a column something will eventually
#: UPDATE.
_BALANCE_COLUMN = re.compile(
    r"(?:^\s*|\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
    r"\"?((?:[A-Za-z_][A-Za-z0-9_]*)?balance[A-Za-z0-9_]*)\"?\s+"
    r"(?:NUMERIC|DECIMAL|BIGINT|INTEGER|INT|SMALLINT|TEXT|MONEY|REAL)",
    re.IGNORECASE | re.MULTILINE,
)


def balance_updates_in(source: str) -> list[str]:
    """Names of balance columns this source UPDATEs. Empty is the only pass."""
    found: list[str] = []
    for statement in _UPDATE_BODY.finditer(source):
        body = statement.group(1)
        if not re.search(r"\bSET\b", body, re.IGNORECASE):
            continue
        for target in _SET_TARGET.finditer(body):
            column = target.group(1)
            if "balance" in column.lower():
                found.append(column)
    return found


def _scanned_files() -> list[str]:
    paths: list[str] = []
    for root, dirs, files in os.walk(_REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name.endswith(_SCANNED):
                paths.append(os.path.join(root, name))
    return paths


def _read(path: str) -> str:
    # encoding named explicitly: cp1252 is the Windows default and it raises on
    # perfectly ordinary files. `errors="replace"` because a scanner that dies
    # on one stray byte is a fence that quietly stops fencing.
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


class TestNoCodePathUpdatesABalanceColumn:
    """CP-6 acceptance clause 1, and §3.4's rule: *"Never UPDATE a balance
    column. Balance is SUM(credit_ledger.delta)."*

    Structural rather than by example, per R7: an example test proves that the
    one path it exercises does not do it, and the path that does it is by
    definition the one nobody wrote a test for. The audit trail is worth most
    at exactly the moment a customer disputes a charge, which is exactly when a
    mutable balance has already destroyed it.
    """

    def test_no_source_file_in_the_tree_updates_a_balance_column(self):
        offenders: dict[str, list[str]] = {}
        for path in _scanned_files():
            if os.path.abspath(path) == os.path.abspath(__file__):
                # This file carries the synthetic violations below, which is
                # the one place they are allowed to exist.
                continue
            hits = balance_updates_in(_read(path))
            if hits:
                offenders[os.path.relpath(path, _REPO_ROOT)] = hits
        assert offenders == {}, (
            "a balance column is being UPDATEd — balance is "
            "SUM(credit_ledger.delta) and the ledger is append-only "
            "(customer_console.md §3.4)"
        )

    def test_the_customer_console_schema_declares_no_balance_column(self):
        ladder = os.path.join(_REPO_ROOT, "infra", "customer_console")
        declared: dict[str, list[str]] = {}
        for name in sorted(os.listdir(ladder)):
            if not name.endswith(".sql"):
                continue
            hits = _BALANCE_COLUMN.findall(_read(os.path.join(ladder, name)))
            if hits:
                declared[name] = hits
        assert declared == {}, (
            "the Customer Console ladder declares a balance column; §3.4's "
            "sketch lists balance_after and 001 omits it deliberately"
        )

    def test_the_fence_is_not_blind(self):
        """The fence tested against synthetic sources, one per shape.

        Without this, "no offenders" is indistinguishable from "the regex
        stopped matching", which is how a guard goes quietly dead.
        """
        assert balance_updates_in("UPDATE credit_ledger SET balance_after = 5 WHERE id = 1") == [
            "balance_after"
        ]
        # Not the first assignment in the SET list.
        assert balance_updates_in("UPDATE organization SET name = 'x', credit_balance = 0") == [
            "credit_balance"
        ]
        # Multi-line, as real SQL in this repo is written.
        assert balance_updates_in(
            'conn.execute(text("""\n'
            "    UPDATE credit_ledger\n"
            "       SET balance = :b\n"
            '     WHERE organization_id = :o"""))'
        ) == ["balance"]

    def test_the_fence_does_not_fire_on_the_live_near_miss(self):
        # infra/postgres/42_email_model_roles.sql really does contain this. A
        # fence that flags it gets deleted by the first person it annoys.
        assert (
            balance_updates_in("UPDATE email_assistant_settings SET chat_model = 'tier-balanced'")
            == []
        )

    def test_the_column_fence_is_not_blind(self):
        assert _BALANCE_COLUMN.findall("    balance_after   NUMERIC(14, 4),") == ["balance_after"]
        assert _BALANCE_COLUMN.findall(
            "ALTER TABLE credit_ledger ADD COLUMN IF NOT EXISTS balance NUMERIC"
        ) == ["balance"]
        # The word in prose, or in a comment, is not a column.
        assert (
            _BALANCE_COLUMN.findall(
                "-- Balance is SUM(delta); there is no balance column to update."
            )
            == []
        )


# ── The usage partition (credit_pricing.md §3, slice 1) ─────────────────────
#
# 🔴 **The fourth expensive failure mode, added 2026-09-04.** `cached_tokens`
# is a SUBSET of `prompt_tokens` under the OpenAI-compatible convention and a
# SIBLING of them under the Anthropic one. `fresh_prompt_tokens` subtracts,
# which is right for the first and wrong for the second — and it used to clamp
# the result at zero, so the wrong answer never reached anybody.

class TestUsagePartition:
    """A cached count larger than the prompt total must refuse, never clamp."""

    def test_a_sibling_convention_report_refuses_instead_of_undercharging(self):
        """The measured 27 % undercharge, now an exception.

        These are the numbers from the specification: one real call of 8000
        prompt tokens with a 6000-token cached prefix. Reported as a sibling
        the prompt total reads 2000, and the old clamp billed 183.60 credits
        where 251.60 was owed.
        """
        with pytest.raises(UsagePartitionError) as exc:
            TokenUsage(prompt_tokens=2000, cached_tokens=6000,
                       completion_tokens=800)
        # The message must name BOTH counts. An alarm that says only "bad
        # usage" cannot be triaged against the row it came from.
        assert "6000" in str(exc.value)
        assert "2000" in str(exc.value)

    def test_the_subset_convention_still_prices_exactly_as_before(self):
        """⚠️ The guard must not move a single legitimate number.

        This is the same 8000/6000/800 call read correctly. It is the
        regression fence on the whole change: if this figure moves, the assert
        has started charging people differently rather than refusing the
        impossible.
        """
        usage = TokenUsage(prompt_tokens=8000, cached_tokens=6000,
                           completion_tokens=800)
        assert usage.fresh_prompt_tokens == 2000
        card = RateCard(
            model="deepseek/deepseek-v4-pro",
            input_per_1m=Decimal("34000"),
            cached_input_per_1m=Decimal("3400"),
            output_per_1m=Decimal("204000"),
            pricing_mode="priced",
        )
        assert quantize_credits(rate_call(card, usage)) == Decimal("251.6000")

    def test_equal_counts_are_legal(self):
        """A fully cached prompt is ordinary, not an error. Fresh is zero."""
        usage = TokenUsage(prompt_tokens=6000, cached_tokens=6000)
        assert usage.fresh_prompt_tokens == 0

    def test_no_cached_count_is_legal(self):
        """The common case: a provider that reports no cache at all."""
        assert TokenUsage(prompt_tokens=8000).fresh_prompt_tokens == 8000


# ── The unbilled fleet read (credit_pricing.md §3, migrations 022 and 025) ──


class TestTheUnbilledFleetRead:
    """🔴 A leak bills ZERO, and a zero sorts off a spend-ordered page.

    So the operator's total cannot come from the page. `usage_by_org` orders
    by credits descending and caps at `SPEND_PAGE_SIZE`; an unbilled call
    contributes no credits by definition, which puts the leaking organization
    LAST. The worse the leak, the more certainly it hides.

    Measured 2026-09-05 on a scratch database: two organizations with a
    faulted call sat past position 5900 of 5988, and the page summed to zero
    while the fleet held two.
    """

    def test_the_read_is_uncapped_and_takes_no_limit(self):
        """The property, read off the signature.

        A `limit` here would re-introduce the bug: whatever the bound, the
        leaking organization is the one it drops.
        """
        import inspect

        from customer_console import store

        params = inspect.signature(store.unbilled_fleet_total).parameters
        assert "limit" not in params, (
            "unbilled_fleet_total must not take a limit — a leak bills zero "
            "and a capped read drops exactly the row it exists to find"
        )
        assert set(params) == {"conn", "days"}

    def test_it_counts_organizations_AND_calls(self):
        """One customer with 400 leaks is a broken integration. 400 customers
        with one each is a broken provider. One number cannot say which."""
        import inspect

        from customer_console import store

        src = inspect.getsource(store.unbilled_fleet_total)
        assert "COUNT(DISTINCT organization_id)" in src
        assert "COUNT(*)" in src

    def test_it_reads_metering_fault_and_never_refusal_reason(self):
        """⚠️ The two are opposites and the SQL must not confuse them.

        A refusal is a customer we said no to — they got nothing and owe
        nothing. A fault is a customer we said yes to and did not charge.
        """
        import inspect

        from customer_console import store

        src = inspect.getsource(store.unbilled_fleet_total)
        assert "metering_fault IS NOT NULL" in src
        assert "refusal_reason" not in src


# ── The per-operation floor (credit_pricing.md §5.4, slice 5) ───────────────


class TestTheChargeFloor:
    """🔴 The floor exists to cover overhead, and `tier-embed` must escape it.

    One embedding costs a fraction of a credit. A five-credit floor per call
    charges **50000 credits to index 10000 documents** against perhaps 200
    credits of real value — a 250x overcharge on the one task a customer runs
    in bulk by design.
    """

    def test_it_SHIPS_INERT_and_the_owner_arms_it(self, monkeypatch):
        """⚠️ The mechanism is an agent's. The FIGURE is the owner's (H-42).

        The first build of this slice shipped it at 5 and turned thirteen
        suites red, each one the floor working correctly on a number nobody
        had agreed to.
        """
        from customer_console import credits as c

        monkeypatch.delenv(c.MIN_CHARGE_ENV, raising=False)
        assert c.min_charge() == 0
        assert c.floor_charge(Decimal("0.4"), task="chat") == Decimal("0.4")

    def test_once_armed_it_floors_a_tiny_charge(self, monkeypatch):
        from customer_console import credits as c

        monkeypatch.setenv(c.MIN_CHARGE_ENV, "5")
        assert c.floor_charge(Decimal("0.4"), task="chat") == Decimal(5)
        assert c.floor_charge(Decimal("423"), task="chat") == Decimal("423")

    def test_EMBED_is_exempt_even_when_armed(self, monkeypatch):
        from customer_console import credits as c

        monkeypatch.setenv(c.MIN_CHARGE_ENV, "5")
        assert c.floor_charge(Decimal("0.4"), task="embed") == Decimal("0.4")

    def test_the_exemption_is_keyed_on_the_TASK_not_the_tier(self):
        """⚠️ D61 makes tiers free text and tasks an allowlist.

        A second embedding tier would silently lose the exemption if this read
        a tier slug.
        """
        from customer_console import credits as c

        assert c.NO_MIN_CHARGE_TASKS == frozenset({"embed"})

    def test_a_ZERO_stays_zero_even_when_armed(self, monkeypatch):
        """⚠️ An absorbed task (D19.2) and an unpriced card both rate to
        nothing on purpose. Lifting either to five credits invents a charge."""
        from customer_console import credits as c

        monkeypatch.setenv(c.MIN_CHARGE_ENV, "5")
        assert c.floor_charge(Decimal(0), task="chat") == 0

    def test_a_broken_value_reads_as_ZERO_and_never_raises(self, monkeypatch):
        """A misconfigured floor must not fail completions."""
        from customer_console import credits as c

        for bad in ("banana", "-5", ""):
            monkeypatch.setenv(c.MIN_CHARGE_ENV, bad)
            assert c.min_charge() == 0, bad
