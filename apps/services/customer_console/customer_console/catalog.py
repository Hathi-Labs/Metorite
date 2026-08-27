"""The operator's model catalog — validation for capabilities, bindings, rates.

Spec: ``project-docs/specs/customer_console.md`` §6A CP-10 slice 3 · §6A.5 (the
INSERT-only write contract) · §6A.9 · D60 · D61.

**Pure functions only.** Every rule here is decided without a database, so the
rules can be tested without one and the routes stay thin. The database enforces
the same shapes again through foreign keys and checks — belt and braces, on the
argument that a rate card is money.

⚠️ **THE WRITE CONTRACT IS INSERT-ONLY** (§6A.5). Re-pointing a tier and
re-pricing a model are both *appends* with an ``effective_from``. A mutable rate
card is the same defect as a balance column: it destroys the audit trail at
exactly the moment a customer disputes a charge, which is the only moment it
matters. There is deliberately no ``update_*`` function in this module, and
adding one is not a refactor.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The provider verbs we know how to call. Data rather than a frozenset in the
#: database (`model_capability.invocation`); this list is what an operator may
#: choose FROM, so a typo cannot invent a verb litellm has never heard of.
KNOWN_INVOCATIONS = frozenset({
    "acompletion",
    "aembedding",
    "atranscription",
    "aspeech",
    "aimage_generation",
})

#: Only these tasks stream (§6A.9 rule 4). A `transcribe` capability claiming
#: to stream would have the Router open a stream the provider never sends.
STREAMABLE_TASKS = frozenset({"chat", "speak"})

#: `pricing_mode` (D61, G-4). A zero cannot carry three meanings.
PRICING_MODES = frozenset({"unpriced", "absorbed", "priced"})


class CatalogRefused(Exception):
    """A catalog write the console refuses, with a reason for the operator."""


@dataclass(frozen=True)
class RateProposal:
    """One proposed rate-card row, before it reaches the database."""

    model: str
    task: str
    unit: str
    pricing_mode: str
    input_per_1k: Decimal = Decimal(0)
    output_per_1k: Decimal = Decimal(0)
    cached_input_per_1k: Decimal = Decimal(0)
    credits_per_unit: Decimal = Decimal(0)

    @property
    def all_rates_zero(self) -> bool:
        return not any((
            self.input_per_1k, self.output_per_1k,
            self.cached_input_per_1k, self.credits_per_unit,
        ))


def check_invocation(invocation: str) -> str:
    """The provider verb must be one we can actually call."""
    verb = (invocation or "").strip()
    if verb not in KNOWN_INVOCATIONS:
        raise CatalogRefused(
            f"unknown invocation {verb!r}; expected one of "
            f"{', '.join(sorted(KNOWN_INVOCATIONS))}"
        )
    return verb


def check_streams(task: str, streams: bool) -> bool:
    """Only ``chat`` and ``speak`` stream (§6A.9 rule 4).

    A capability that claimed otherwise would have the Router hold a connection
    open for frames the provider is never going to send.
    """
    if streams and task not in STREAMABLE_TASKS:
        raise CatalogRefused(
            f"task {task!r} does not stream; only "
            f"{', '.join(sorted(STREAMABLE_TASKS))} do"
        )
    return streams


def check_rate(proposal: RateProposal, *, natural_unit: str) -> RateProposal:
    """Refuse a rate card that cannot mean what it says.

    Three refusals, each for a mistake that would otherwise bill somebody:

    1. **The unit must be the task's own.** ``transcribe`` is sold per minute
       of audio (D19.2 says so in terms). Pricing it per 1k tokens produces a
       number, and a plausible one, and a wrong one — §6A.9 records this as the
       reason ``task_catalog`` carries ``natural_unit`` at all.
    2. **`priced` with every rate at zero is a mistake, not a price.** That is
       what ``absorbed`` is for (D19.2's embeddings), and the two must not be
       expressible the same way — the whole point of G-4.
    3. **A negative rate would CREDIT a customer for using the product.**
    """
    if proposal.pricing_mode not in PRICING_MODES:
        raise CatalogRefused(
            f"unknown pricing_mode {proposal.pricing_mode!r}; expected one of "
            f"{', '.join(sorted(PRICING_MODES))}"
        )

    if proposal.unit != natural_unit:
        raise CatalogRefused(
            f"task {proposal.task!r} is priced in {natural_unit!r}, "
            f"not {proposal.unit!r}"
        )

    for name, value in (
        ("input_per_1k", proposal.input_per_1k),
        ("output_per_1k", proposal.output_per_1k),
        ("cached_input_per_1k", proposal.cached_input_per_1k),
        ("credits_per_unit", proposal.credits_per_unit),
    ):
        if value < 0:
            raise CatalogRefused(f"{name} may not be negative")

    if proposal.pricing_mode == "priced" and proposal.all_rates_zero:
        raise CatalogRefused(
            "pricing_mode 'priced' with every rate at zero says nothing. "
            "Use 'absorbed' for a task the seat price covers, or set a rate"
        )

    if proposal.pricing_mode != "priced" and not proposal.all_rates_zero:
        # ⚠️ The mirror of the rule above, and the one that would otherwise
        # ship a real price nobody meant to switch on. A card carrying numbers
        # under `unpriced` reads as a draft — and the ladder fence
        # (`test_the_rate_card_ships_unpriced`) counts exactly this shape.
        raise CatalogRefused(
            f"pricing_mode {proposal.pricing_mode!r} carries a non-zero rate. "
            "Set 'priced' to charge it, or zero the rates"
        )

    return proposal


def unbound_capabilities(
    capabilities: list[tuple[str, str]],
    bindings: list[tuple[str, str]],
) -> list[dict[str, str]]:
    """The GAP: what a model can do that we have not bound to any tier.

    ⚠️ **Capability is not availability** (§6A.9 rule 3), and this difference is
    the operator's most valuable view — *"gpt-4o can generate images and we
    have not bound it to anything."* Neither table shows it alone.

    ``capabilities`` and ``bindings`` are ``(model, task)`` pairs.
    """
    bound = set(bindings)
    return [
        {"model": model, "task": task}
        for model, task in sorted(set(capabilities))
        if (model, task) not in bound
    ]


def unserved_bindings(
    capabilities: list[tuple[str, str]],
    bindings: list[tuple[str, str]],
) -> list[dict[str, str]]:
    """The OTHER gap, and the dangerous one: bound but not capable.

    A tier pointing at a model that declares no capability for that task means
    the Router resolves a model and then cannot decide which verb to call. That
    is a 500 waiting for the first request, and it is invisible in either table
    on its own.
    """
    capable = set(capabilities)
    return [
        {"model": model, "task": task}
        for model, task in sorted(set(bindings))
        if (model, task) not in capable
    ]
