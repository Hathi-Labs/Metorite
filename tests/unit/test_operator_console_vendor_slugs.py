"""Every vendor the operator console offers must be a slug litellm answers to.

🔴 **This fence exists because two of the eleven original entries were dead.**
``providerGuides.ts`` offered ``google`` for Gemini and ``together`` for
Together AI. litellm calls them ``gemini`` and ``together_ai``, and the Router
looks a credential up on the model id's first path segment::

    vendor = step.model.split("/", 1)[0]          # main.py, chat_completions
    provider_credential(conn, provider=vendor, …)

So a wrong slug does not fail at install time. It fails four steps later: the
key installs, the model declares, the tier binds, and the first customer
request answers ``503 no provider credential configured for 'gemini'`` — with
a card on the providers page still saying the vendor is armed.

⚠️ **This is a SOURCE test, and it needs no database.** It reads the TypeScript
registry as text, because the alternative — a JSON file both sides import —
would be a second copy of the list, and a second copy goes stale.

⚠️ **The parse must fail loudly when it finds nothing.** A regex that silently
matches zero lines is how a fence goes blind, which has happened eight times in
this tree. ``test_the_parse_itself_still_works`` is the guard on the guard.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "workbench"
    / "operator_console"
    / "src"
    / "lib"
    / "providerGuides.ts"
)

#: A top-level key of the PROVIDER_GUIDES object literal. Two spaces of
#: indent, then the slug, then the opening brace of its guide.
_SLUG = re.compile(r"^  ([a-z0-9_]+): \{$")

#: Below this many entries the parse has plainly broken rather than the list
#: having shrunk. Seventeen ship today.
_MIN_EXPECTED = 12


def _slugs() -> list[str]:
    """The vendor slugs, read out of the registry between its own braces.

    ⚠️ Bounded to the object literal. Scanning the whole file would pick up
    the header prose, which names ``google`` on purpose — it records the bug
    this test was written for.
    """
    text = _REGISTRY.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(
            i for i, ln in enumerate(lines)
            if ln.startswith("export const PROVIDER_GUIDES")
        )
    except StopIteration:  # pragma: no cover - the guard below reports it
        return []
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] == "};"),
        len(lines),
    )
    found = [m.group(1) for ln in lines[start:end] if (m := _SLUG.match(ln))]
    return found


def test_the_parse_itself_still_works() -> None:
    """The guard on the guard. A blind fence passes everything."""
    found = _slugs()
    assert len(found) >= _MIN_EXPECTED, (
        f"parsed only {len(found)} vendor slugs from {_REGISTRY.name} "
        f"({found}). Either the registry shrank or the shape of the file "
        "changed and this test now checks nothing."
    )
    assert "anthropic" in found, "the parse missed a slug certainly there"


def test_every_offered_vendor_is_a_litellm_provider() -> None:
    """🔴 The one rule. A slug litellm does not know can never be called."""
    litellm = pytest.importorskip("litellm")
    known = {getattr(p, "value", p) for p in litellm.provider_list}

    unknown = [s for s in _slugs() if s not in known]
    assert not unknown, (
        f"{unknown} is offered on the providers page but is not a litellm "
        "provider id. The Router resolves a vendor as the first path segment "
        "of the model id, so a key installed under this slug is never found. "
        "Use litellm's own id — 'gemini' not 'google', 'together_ai' not "
        "'together'."
    )


def test_the_two_slugs_that_were_actually_wrong_stay_gone() -> None:
    """Named, because a plausible wrong answer comes back on its own.

    Both read as correct to anybody who has not checked litellm's list, and
    both were written by somebody being careful.
    """
    found = set(_slugs())
    assert "google" not in found, "Gemini's litellm id is 'gemini'"
    assert "together" not in found, "Together AI's litellm id is 'together_ai'"


_SAMPLE = _REGISTRY.parent / "sample.ts"

#: A model id's vendor half, wherever one appears in the sample catalogue —
#: `M("gemini/gemini-2.5-flash", ...)`, `model: "groq/llama-3.3-70b"`,
#: `provider: "anthropic"`.
_SAMPLE_VENDOR = re.compile(
    r'(?:M\(|model: |provider: |from: |to: )"([a-z0-9_.-]+)(?:/|")'
)


def test_the_designed_placeholder_teaches_the_right_slugs() -> None:
    """⚠️ Sample data is READ as an example, so a wrong slug in it propagates.

    The placeholder carried ``google/gemini-2.5-flash`` and a ``google``
    provider account. Both rendered convincingly and both were the exact
    mistake this file exists to stop — an operator copying the shape off the
    sample screen would have installed a key nothing could find.
    """
    litellm = pytest.importorskip("litellm")
    known = {getattr(p, "value", p) for p in litellm.provider_list}

    text = _SAMPLE.read_text(encoding="utf-8")
    found = {m.group(1) for m in _SAMPLE_VENDOR.finditer(text)}
    assert len(found) >= 5, (
        f"parsed only {found} from sample.ts — the fence has gone blind"
    )
    unknown = sorted(s for s in found if s not in known)
    assert not unknown, (
        f"{unknown} appears as a vendor in the designed placeholder but is "
        "not a litellm provider id. Sample data that models a wrong slug "
        "teaches the wrong slug."
    )


def test_every_slug_passes_the_console_admission_rule() -> None:
    """The page must not offer a vendor the Console would refuse to install."""
    from customer_console.provider_keys import CredentialRefused, check_provider

    for slug in _slugs():
        try:
            assert check_provider(slug) == slug
        except CredentialRefused as exc:  # pragma: no cover - a real failure
            pytest.fail(f"{slug!r} is offered but refused on install: {exc}")
