"""The organization-slug vocabulary, pinned across two languages (CP-2c 4a; D51).

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1f (owner ruling **B7**,
slice-1 done-when 8) · ``customer_console.md`` §CP-2c item 3's reserved-label
block and done-when 4a.

⚠️ **Why this file exists at all.** The reserved-label set has TWO runtime
consumers in two languages — ``workbench/control_plane/src/lib/subdomain.ts``
(the canonical vocabulary module, imported by the signup form) and
``gateway/routes/signup.py``'s slug gate (which decides whether a customer may
register one). They must be the same set: a label reserved on one side and
registrable on the other is exactly the collision the ruling closes, only harder
to see. Rather than a convention nobody re-checks, this suite **reads the
TypeScript** and pins the Python to it — the
``test_seed_status_colours_match_the_shared_vocabulary`` idiom, adopted for the
reason ``workbench/control_plane/AGENTS.md`` rule 5 gives: *a mirror goes stale
and then lies.*

Direction is deliberate: the TypeScript is canonical because a slug is a public
identifier the workbench renders first (and the set was born of DNS-safety —
kept under D51, which withdrew subdomain hosting but not the vocabulary).
Either side drifting is red, so one direction suffices.

A **third** consumer joined on 2026-08-24 (repair round 1) and is pinned
differently on purpose: ``app/signup/SignUpForm.tsx`` *imports* both the reserved
list and ``SLUG_RE`` from the canonical module, so the case below asserts the
import and the ABSENCE of a local literal rather than comparing yet another
copy — there is no fourth place for the pattern to be written down.

**Deliberately DB-free**, like ``test_console_dependency_boundary.py``: a
structural fence that skips whenever a database is absent is a fence that was
never there. Nothing here opens a session.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from gateway.routes import signup as route

_ROOT = Path(__file__).resolve().parents[2]
_SUBDOMAIN_TS = _ROOT / "workbench/control_plane/src/lib/subdomain.ts"
_SIGNUP_FORM_TSX = _ROOT / "workbench/control_plane/src/app/signup/SignUpForm.tsx"
_GATEWAY_MAIN = _ROOT / "apps/services/gateway/gateway/main.py"
#: The FOURTH consumer, added 2026-09-15: the Customer Console's
#: ``ProvisionRequest`` validator — the one door BOTH provisioning arms pass
#: through, and the arm an operator drives had no slug rule at all until then.
#: Read as SOURCE rather than imported: importing ``customer_console.main``
#: drags litellm and the payment seam into a fence this module's own header
#: keeps deliberately DB-free and dependency-light.
_CONSOLE_MAIN = _ROOT / ("apps/services/customer_console/customer_console/main.py")
#: The FIFTH, pinned by ABSENCE: the Operator Console's slug suggestion, which
#: carried a second and drifted implementation until 2026-09-15.
_OPERATOR_SLUG_TS = _ROOT / "workbench/operator_console/src/lib/slug.ts"
_OPERATOR_FORMAT_TS = _ROOT / "workbench/operator_console/src/lib/format.ts"


def _read(path: Path) -> str:
    # encoding="utf-8" EXPLICITLY: Windows is the primary dev box and cp1252 is
    # the default, which crashes on the ⚠ these files carry (root CLAUDE.md §6).
    return path.read_text(encoding="utf-8")


def _ts_array(source: str, name: str) -> list[str]:
    """The string literals of an exported TS array, in declaration order."""
    match = re.search(
        rf"export const {name}\s*:[^=]*=\s*\[(.*?)\]\s*;",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} is not an exported array literal any more"
    return re.findall(r'"([^"]*)"', match.group(1))


def _py_frozenset(source: str, name: str) -> list[str]:
    """The string literals of a module-level ``frozenset({...})``, in order.

    Source-parsed, not imported — see ``_CONSOLE_MAIN``'s note. Asserts the
    declaration is still findable, so a restructure is RED here rather than a
    silently empty set that makes every comparison below trivially true.

    ⚠️ **Tolerant of WHITESPACE between the call and the brace, added
    2026-09-18.** The pattern demanded ``frozenset({`` with nothing between
    them. `ruff-format` legitimately writes the same declaration as
    ``frozenset(\\n    {`` once the file it lives in is formatted, and this
    test then reported "not a module-level frozenset any more" about a
    declaration that had not changed at all.

    🔴 **The assertion's real job is unchanged and must stay.** It exists to
    go RED when somebody builds this set some other way — a comprehension, a
    loop, a read from a file — because then the comparisons below would pass
    against an empty list. A reformat is not that, and a fence that cannot
    tell the two apart teaches people to edit the fence.
    """
    match = re.search(
        rf"^{name}\s*=\s*frozenset\(\s*\{{(.*?)\}}\s*,?\s*\)",
        source,
        re.DOTALL | re.M,
    )
    assert match is not None, f"{name} is not a module-level frozenset any more"
    found = re.findall(r'"([^"]*)"', match.group(1))
    # 🔴 **The empty case, which the docstring above always promised to catch
    # and did not.** `frozenset({s for s in SOURCE})` MATCHES the pattern and
    # yields no string literals, so every comparison below would run against
    # an empty list and pass. Measured 2026-09-18, against the pattern as it
    # stood before this line — so this is an old hole, found while widening
    # the pattern for whitespace and closed here rather than left for the
    # person who eventually writes that comprehension.
    assert found, (
        f"{name} matched, but carries no string literals — it is probably "
        "built dynamically now, and every comparison below would pass "
        "against an empty set"
    )
    return found


def _py_pattern(source: str, name: str) -> str:
    """The raw pattern of a module-level ``re.compile(r"…")``."""
    match = re.search(rf'^{name}\s*=\s*re\.compile\(r"(.+?)"\)', source, re.M)
    assert match is not None, f"{name} is not a module-level re.compile any more"
    return match.group(1)


class TestTheReservedVocabularyIsOneList:
    """Owner ruling B7 — one set, two runtime consumers, no hand-copied mirror."""

    def test_the_typescript_declaration_is_still_parseable(self):
        # Non-vacuity, and the first thing to break if the canonical file is
        # restructured: an unparseable declaration must be a RED test here, not
        # a silently empty set that makes every comparison below trivially true.
        labels = _ts_array(_read(_SUBDOMAIN_TS), "RESERVED_LABELS")
        assert len(labels) >= 10
        assert "api" in labels
        assert "app" in labels

    def test_the_gateway_set_equals_the_typescript_list(self):
        """The pin. Editing one side without the other fails HERE, by name."""
        canonical = set(_ts_array(_read(_SUBDOMAIN_TS), "RESERVED_LABELS"))
        enforced = route._RESERVED_SLUGS
        assert canonical == enforced

    def test_every_reserved_label_is_itself_a_well_formed_slug(self):
        """Otherwise the reserved check is dead code behind the shape check.

        ``_slug_shape_refusal`` runs ``_SLUG_RE`` FIRST and only then consults
        the reserved set, so a reserved entry that could never pass the regex
        would be unreachable — a rule that reads enforced and is not.
        """
        for label in sorted(route._RESERVED_SLUGS):
            assert route._SLUG_RE.fullmatch(label), label

    def test_the_slug_SHAPE_is_the_same_rule_on_both_sides(self):
        """The host parser and the signup gate must agree on what a slug IS, or
        a name accepted at signup is unrepresentable as a hostname."""
        ts = _read(_SUBDOMAIN_TS)
        match = re.search(r"export const SLUG_RE\s*=\s*/(.+?)/;", ts)
        assert match is not None, "SLUG_RE is no longer an exported literal"
        assert match.group(1) == route._SLUG_RE.pattern

    def test_the_signup_FORM_imports_the_shape_rather_than_copying_it(self):
        """The third consumer, pinned by IMPORT rather than by comparison.

        Added 2026-08-24 (repair round 1, diff-review P2): ``SignUpForm.tsx``
        carried its own ``const SLUG_RE = /…/`` literal, byte-identical to this
        one on the day it was written — which is the only day a copy ever is.
        Pinning it by *equality* would have been the fourth place the pattern is
        written down; pinning the IMPORT means there is nothing left to drift,
        and this case is what makes a re-grown local literal red rather than
        merely untidy.
        """
        form = _read(_SIGNUP_FORM_TSX)
        assert re.search(r'import \{[^}]*\bSLUG_RE\b[^}]*\} from "@/lib/subdomain"', form), (
            "SignUpForm must IMPORT SLUG_RE from the one vocabulary"
        )
        # No local re-declaration of either half, under any name: a `const
        # SLUG_RE = /…/` here is the mirror, and so is a fresh regex literal
        # spelling the same DNS-label shape.
        assert not re.search(r"const\s+SLUG_RE\s*=\s*/", form)
        assert r"[a-z0-9]([a-z0-9-]" not in form
        # Non-vacuity: the form still USES the imported rule, so the assertions
        # above are not passing over a file that stopped validating slugs.
        assert "SLUG_RE.test(" in form

    def test_the_CONSOLE_set_equals_the_typescript_list(self):
        """The fourth consumer — and the arm that had NO rule until 2026-09-15.

        ``POST /orgs/provision`` is the one door both provisioning arms pass
        through. The gateway's self-serve arm shape-checked its slug before
        forwarding; the OPERATOR arm reached ``ProvisionRequest`` directly, where
        ``slug`` was a bare ``str``. So an operator could create ``api`` — the
        slug that names the gateway's own hostname — which is the live defect
        owner ruling B7 closed on the other arm only.
        """
        canonical = set(_ts_array(_read(_SUBDOMAIN_TS), "RESERVED_LABELS"))
        assert set(_py_frozenset(_read(_CONSOLE_MAIN), "_RESERVED_SLUGS")) == (canonical)

    def test_the_CONSOLE_shape_is_the_same_rule_as_the_gateway(self):
        console = _py_pattern(_read(_CONSOLE_MAIN), "_SLUG_RE")
        assert console == route._SLUG_RE.pattern

    def test_the_CONSOLE_actually_ENFORCES_the_vocabulary_it_declares(self):
        """Non-vacuity: two constants nothing reads would pass every case above.

        The two cases before this one compare declarations. This one asserts
        ``ProvisionRequest`` still runs them — a validator deleted while the
        constants stayed is precisely the shape that reads enforced and is not.
        """
        source = _read(_CONSOLE_MAIN)
        assert '@field_validator("slug")' in source
        assert "_SLUG_RE.fullmatch(slug)" in source
        assert "slug in _RESERVED_SLUGS" in source

    def test_the_OPERATOR_console_keeps_no_second_slug_implementation(self):
        """Pinned by ABSENCE — the fifth consumer, repaired 2026-09-15.

        ``lib/format.ts`` carried its own ``suggestSlug``: it cut at 40
        characters (the canonical one cuts at 63) and never re-trimmed a
        trailing hyphen after the cut, so it could suggest a label the Console
        now refuses. Two apps, two answers to one question — root ``CLAUDE.md``
        §4's parallel-seam defect by name.

        ⚠️ **The operator console may not IMPORT the canonical module.** D35.2
        makes it a different application by construction, with no shared
        workbench config. So it carries a fenced copy in ``lib/slug.ts``, pinned
        by the two cases below, and ``format.ts`` must not re-grow one.
        """
        fmt = _read(_OPERATOR_FORMAT_TS)
        assert "export function suggestSlug" not in fmt
        assert r"[^a-z0-9]+" not in fmt

    def test_the_OPERATOR_console_copy_is_pinned_to_the_canonical_one(self):
        canonical_ts = _read(_SUBDOMAIN_TS)
        operator_ts = _read(_OPERATOR_SLUG_TS)

        canonical_re = re.search(r"export const SLUG_RE\s*=\s*/(.+?)/;", canonical_ts)
        operator_re = re.search(r"export const SLUG_RE\s*=\s*/(.+?)/;", operator_ts)
        assert operator_re is not None, "the operator copy lost SLUG_RE"
        assert canonical_re is not None
        assert operator_re.group(1) == canonical_re.group(1)

        assert set(_ts_array(operator_ts, "RESERVED_LABELS")) == set(
            _ts_array(canonical_ts, "RESERVED_LABELS")
        )


class TestTheBrowserNeverTalksToTheGatewayDirectly:
    """Slice-1 done-when 8 — the gateway half.

    The browser-tier half (no ``NEXT_PUBLIC_GATEWAY*`` reader) is fenced in
    ``src/lib/subdomain.test.ts``, where it can walk the TypeScript tree. This
    is the other side of the same claim: MT-1f introduces a wildcard of
    hostnames, and a permissive CORS origin would turn every one of them into an
    accepted browser origin for the API.
    """

    def test_the_cors_allow_list_carries_no_wildcard_or_regex_origin(self):
        source = _read(_GATEWAY_MAIN)
        block = source[source.index("allow_origins=[") :]
        block = block[: block.index("]")]
        assert '"*"' not in block
        assert "'*'" not in block
        # A regex origin is the same hole spelled differently — and it is the
        # obvious "fix" somebody reaches for the day wildcard hostnames exist.
        assert "allow_origin_regex" not in source
        # Non-vacuity: the allow-list still names the origins it always did, so
        # this is not passing against an empty slice.
        assert "localhost:3001" in block
