"""``POST /orgs/provision`` refuses a slug the platform could never host.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1f (owner ruling
**B7**, the vocabulary) · ``customer_console.md`` §6 **CP-2i** (the arm this
closes).

⚠️ **The gap this closes.** ``/orgs/provision`` is a DUAL-ARM door. The
deployment-key arm is driven by ``gateway/routes/signup.py``, which shape-checks
the slug against ``_SLUG_RE`` + ``_RESERVED_SLUGS`` and answers
``InvalidSlug``/``ReservedSlug`` before it ever forwards. The **operator** arm
reaches ``ProvisionRequest`` directly, and ``slug`` was a bare ``str`` — so an
operator could create ``api``, ``www`` or ``Acme_Co``. The first two name
hostnames the platform already serves; the third is not a DNS label at all. The
slug is the cross-plane JOIN KEY and MT-1f's future subdomain, so a value that
cannot be a hostname is a defect wherever it enters.

Owner ruling B7 closed this on the self-serve arm on 2026-08-24 and called it a
LIVE defect. It was only ever closed on one of the two arms.

**Deliberately DB-free.** Pydantic validation is pure Python: it runs before the
handler, opens no session and executes no SQL, so R8 does not apply and a fence
that skipped without Postgres would be a fence that was never there. The
CROSS-LANGUAGE parity of the vocabulary itself lives in
``test_subdomain_host_vocabulary.py``, which can read all four consumers; this
file asserts that the Console ENFORCES what it declares.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from customer_console.main import ProvisionRequest
from pydantic import ValidationError


def _request(slug: str) -> ProvisionRequest:
    """A provision body that is valid in every respect EXCEPT the slug."""
    return ProvisionRequest(
        slug=slug,
        name="Acme Inc",
        owner_email="ada@customer.example",
        deployment_label="gateway",
    )


class TestAWellFormedSlugStillPasses:
    """Non-vacuity first: a validator that refuses everything is not a fence,
    it is an outage, and every refusal case below would pass against one."""

    @pytest.mark.parametrize(
        "slug",
        [
            "acme",
            "fracktal-works",
            "a",
            "a1",
            "a-b-c",
            "x" * 63,
            "default",  # the first-party bootstrap org (migration 157)
        ],
    )
    def test_it_is_accepted(self, slug):
        assert _request(slug).slug == slug


class TestTheSHAPEGate:
    """A slug that is not a DNS label is refused, on EITHER arm."""

    @pytest.mark.parametrize(
        "slug",
        [
            "",  # blank
            "   ",  # whitespace only
            "Acme",  # uppercase
            "ACME",
            "acme co",  # internal space
            "acme_co",  # underscore is not DNS-safe
            "-acme",  # leading hyphen
            "acme-",  # trailing hyphen — what the OLD operator-console
            #            suggestSlug could produce, by slicing at 40
            #            characters and never re-trimming
            "x" * 64,  # one past the label limit
            "acme.co",  # a dot is a label SEPARATOR, not a label character
            "acme/co",
        ],
    )
    def test_it_is_refused(self, slug):
        with pytest.raises(ValidationError):
            _request(slug)

    def test_the_refusal_names_the_rule_rather_than_the_regex(self):
        """An operator reading this has to know what to type instead."""
        with pytest.raises(ValidationError) as exc:
            _request("Acme Co")
        assert "DNS-label-safe" in str(exc.value)


class TestTheRESERVEDGate:
    """Owner ruling B7, now enforced on the arm that never had it."""

    @pytest.mark.parametrize(
        "slug", ["api", "app", "www", "admin", "console", "operator", "signup"]
    )
    def test_a_platform_hostname_is_refused(self, slug):
        # `api` names this very gateway's hostname. Before this validator an
        # operator could register it, and the moment MT-1f's wildcard record
        # exists that is a hostname collision with a live service.
        with pytest.raises(ValidationError):
            _request(slug)

    def test_a_reserved_label_is_not_reported_as_MALFORMED(self):
        """Two causes, two sentences — the split the gateway twin makes between
        ``InvalidSlug`` and ``ReservedSlug``. ``api`` is perfectly well formed,
        so calling it malformed sends the operator to fix a thing that is not
        wrong."""
        with pytest.raises(ValidationError) as exc:
            _request("api")
        message = str(exc.value)
        assert "reserved" in message
        assert "DNS-label-safe" not in message

    def test_the_reserved_set_is_reached_only_AFTER_the_shape_check(self):
        """Ordering, pinned: every reserved label is itself a well-formed slug,
        so a shape check that ran second would make the reserved gate dead code
        — a rule that reads enforced and is not."""
        from customer_console.main import _RESERVED_SLUGS, _SLUG_RE

        for label in sorted(_RESERVED_SLUGS):
            assert _SLUG_RE.fullmatch(label), label


class TestTheSurroundingFieldsAreUntouched:
    """The validator is on ``slug`` alone. A change that quietly tightened the
    rest of the body would break the six CP-2a-era suites for a reason nobody
    asked for."""

    def test_the_optional_billing_fields_still_default_to_None(self):
        request = _request("acme")
        assert request.gstin is None
        assert request.billing_state is None

    def test_core_seats_still_defaults_to_one_and_refuses_zero(self):
        # The Console default. `gateway/routes/signup.py` now SENDS this rather
        # than letting it apply silently — see that route's `_team_size`.
        assert _request("acme").core_seats == 1
        with pytest.raises(ValidationError):
            ProvisionRequest(
                slug="acme",
                name="Acme Inc",
                owner_email="ada@customer.example",
                deployment_label="gateway",
                core_seats=0,
            )

    def test_a_deployment_label_is_still_OPTIONAL_at_the_model(self):
        """Both arms' rules stay in the HANDLER, where one model cannot express
        them (the class docstring's own argument): required under the operator
        scheme, refused under a deployment key."""
        request = ProvisionRequest(
            slug="acme", name="Acme Inc", owner_email="ada@customer.example"
        )
        assert request.deployment_label is None
