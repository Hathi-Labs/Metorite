"""WS-29 MT-1f — the workspace-hostname vocabulary, pinned across two languages.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1f (owner ruling **B7**,
slice-1 done-when 8) · ``customer_console.md`` §CP-2c item 3's reserved-label
block and done-when 4a.

⚠️ **Why this file exists at all.** The reserved-label set has TWO runtime
consumers in two languages — ``workbench/control_plane/src/lib/subdomain.ts``'s
host parser (which decides whether a hostname names a workspace) and
``gateway/routes/signup.py``'s slug gate (which decides whether a customer may
register one). They must be the same set: a label reserved on one side and
registrable on the other is exactly the collision the ruling closes, only harder
to see. Rather than a convention nobody re-checks, this suite **reads the
TypeScript** and pins the Python to it — the
``test_seed_status_colours_match_the_shared_vocabulary`` idiom, adopted for the
reason ``workbench/control_plane/AGENTS.md`` rule 5 gives: *a mirror goes stale
and then lies.*

Direction is deliberate: the TypeScript is canonical because the list exists for
DNS reasons and the host parser is what DNS reaches first. Either side drifting
is red, so one direction suffices.

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
_GATEWAY_MAIN = _ROOT / "apps/services/gateway/gateway/main.py"


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
        block = source[source.index("allow_origins=["):]
        block = block[: block.index("]")]
        assert '"*"' not in block
        assert "'*'" not in block
        # A regex origin is the same hole spelled differently — and it is the
        # obvious "fix" somebody reaches for the day wildcard hostnames exist.
        assert "allow_origin_regex" not in source
        # Non-vacuity: the allow-list still names the origins it always did, so
        # this is not passing against an empty slice.
        assert "localhost:3001" in block
