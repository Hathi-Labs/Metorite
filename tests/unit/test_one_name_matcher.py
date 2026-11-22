"""One matcher of a near name (HRD-18b, ``find_engine.md`` §4.4 rule 3).

"Each near test reads its floor from ``acb_common.name_match`` (HRD-18a). No
module keeps a copy of a floor or a fold." This fence reads the source of the
gateway and of ``acb_skills``, and fails when:

1. A module other than ``name_match.py`` assigns a floor of the matcher:
   ``NEAR_SCORE``, ``NEAR_CANDIDATE_SIMILARITY``, ``NEAR_MIN_LETTERS``,
   ``SOUND_MIN_LETTERS`` or ``SOUND_WEIGHT``.
2. A module holds a copy of the spelling fold: three or more of the pairs of
   ``name_match.VARIANTS`` as literals.
3. A module calls ``pg_trgm`` (``similarity``, ``word_similarity`` or
   ``strict_word_similarity``) and does not import ``name_match``, or keeps a
   floor of its own that :data:`OWN_FLOORS` does not name with its reason.
4. A module defines ``def fold(`` and :data:`OTHER_FOLDS` does not name it
   with its reason. A new near step that folds a name for itself fails here.
5. A near step of HRD-18b does not score with ``name_match``: the three
   ``near`` readers, the engine's choice of a spelling, and the sender retry
   of ``/email/messages``.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from acb_common import name_match

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (
    ROOT / "apps/services/gateway/gateway",
    ROOT / "packages/acb_skills/acb_skills",
)
MATCHER = ROOT / "packages/acb_common/acb_common/name_match.py"

#: The floors of the matcher. Only ``name_match.py`` assigns them.
FLOORS = ("NEAR_SCORE", "NEAR_CANDIDATE_SIMILARITY", "NEAR_MIN_LETTERS",
          "SOUND_MIN_LETTERS", "SOUND_WEIGHT")

#: A ``pg_trgm`` call in SQL text. ``title_similarity(`` is a Python function
#: and does not match.
TRGM = re.compile(r"(?<![\w])(?:strict_word_|word_)?similarity\(")

#: A module constant that reads as a floor: a name with THRESHOLD,
#: SIMILARITY or FLOOR, given a number from 0 to 1.
FLOOR_CONST = re.compile(
    r"^([A-Z_]*(?:THRESHOLD|SIMILARITY|FLOOR)[A-Z_]*)\s*=\s*0?\.\d+", re.MULTILINE)

#: Each own floor of a ``pg_trgm`` module, and why it may stay.
OWN_FLOORS: dict[tuple[str, str], str] = {
    ("routes/email/transport/contacts.py", "FUZZY_THRESHOLD"): (
        "The raw trigram floor of the recipient typeahead, older than the "
        "matcher. Its SQL reads every person of the org (FUZZY_SCAN), and "
        "`_closest` keeps a row at this floor OR at name_match.NEAR_SCORE. So "
        "it can only add a person, never refuse one that the matcher calls near."
    ),
}

#: Each ``def fold(`` that is no fold of a name for a near test, and why.
OTHER_FOLDS: dict[str, str] = {
    "routes/admin/assistant_profile_people.py": (
        "The EXACT fold of the alias and directory tiers: NFKD, no combining "
        "mark, casefold. Its near tier scores with name_match.name_score."
    ),
    "routes/email/automation/insights_extract.py": (
        "White space of a mail fact: no name and no near test."
    ),
    "routes/project_rules_propose.py": (
        "The duplicate test of a rule sentence: no name and no near test."
    ),
}


def _files() -> list[Path]:
    out = [path for base in SCANNED for path in base.rglob("*.py")]
    assert len(out) > 100, "the scan found no source"  # non-vacuity
    return out


def _rel(path: Path) -> str:
    for base in SCANNED:
        if path.is_relative_to(base):
            return path.relative_to(base).as_posix()
    return path.as_posix()


def _assigned(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imports_matcher(source: str) -> bool:
    return bool(re.search(r"from acb_common import [^\n]*\bname_match\b"
                          r"|from acb_common\.name_match import|import acb_common\.name_match",
                          source))


class TestOneFloor:
    def test_only_the_matcher_assigns_a_floor(self):
        found = []
        for path in _files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name in sorted(_assigned(tree) & set(FLOORS)):
                found.append(f"{_rel(path)} assigns {name}")
        assert found == []
        # Non-vacuity: the matcher assigns each one.
        assert set(FLOORS) <= _assigned(ast.parse(MATCHER.read_text(encoding="utf-8")))


class TestOneFold:
    def test_no_module_copies_the_spelling_variants(self):
        found = []
        for path in _files():
            source = path.read_text(encoding="utf-8")
            pairs = sum(
                1 for left, right in name_match.VARIANTS
                if re.search(rf"\(\s*[\"']{left}[\"']\s*,\s*[\"']{right}[\"']\s*\)", source)
            )
            if pairs >= 3:
                found.append(f"{_rel(path)} holds {pairs} pairs of name_match.VARIANTS")
        assert found == []

    def test_each_fold_is_one_the_list_names(self):
        found = sorted(
            _rel(path) for path in _files()
            if re.search(r"^\s*def fold\(", path.read_text(encoding="utf-8"), re.MULTILINE)
        )
        assert found == sorted(OTHER_FOLDS), (
            "a new `def fold(`: fold a name with acb_common.name_match, or name the "
            "fold in OTHER_FOLDS with the reason it is no near test"
        )


class TestEachTrigramFloor:
    def test_a_pg_trgm_module_imports_the_matcher_and_keeps_no_floor_of_its_own(self):
        sites, found = [], []
        for path in _files():
            source = path.read_text(encoding="utf-8")
            if not TRGM.search(source):
                continue
            rel = _rel(path)
            sites.append(rel)
            if not _imports_matcher(source):
                found.append(f"{rel} calls pg_trgm and does not import name_match")
            for name in FLOOR_CONST.findall(source):
                if (rel, name) not in OWN_FLOORS:
                    found.append(f"{rel} keeps its own floor {name}")
        assert found == []
        # Non-vacuity: the two sites of HRD-18a.
        assert {"routes/crm/core.py", "routes/email/transport/contacts.py"} <= set(sites)

    def test_the_crm_candidate_floor_is_the_matchers(self):
        from gateway.routes.crm import core

        source = inspect.getsource(core._near_read)
        assert "name_match.NEAR_CANDIDATE_SIMILARITY" in source
        assert "name_match.NEAR_SCORE" in source


class TestTheNearSteps:
    def test_each_near_reader_scores_with_the_matcher(self):
        from gateway.routes.crm import records
        from gateway.routes.email.transport import contacts
        from gateway.routes.people import directory

        for fn in (records.near_names, directory.near_names, contacts.near_senders):
            assert "name_match.near_lookup" in inspect.getsource(fn), fn.__qualname__

    def test_each_near_source_is_registered(self):
        from gateway.retrieval import sources

        able = {key for key, s in sources.REGISTRY.items() if s.near is not None}
        assert able == {"crm", "email", "people"}

    def test_the_engine_picks_a_spelling_with_the_matcher(self):
        from gateway.retrieval import company
        from gateway.routes.email.transport import messages

        assert "name_match.best_spelling" in inspect.getsource(company.near_asks)
        retry = inspect.getsource(messages._near_from)
        assert "name_match.near_names" in retry and "name_match.best_spelling" in retry
        assert _imports_matcher(inspect.getsource(company))
