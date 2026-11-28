"""CI-R1a (task 4170, ``knowledge_graph.md`` §9.1): one edge vocabulary.

The graph, find and the chat tool read ``acb_common.edge_vocabulary``. Each
list was defined twice before this slice. Each claim below fails when a second
copy comes back, or when a stored relation has a neighbour with no edge code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import edge_vocabulary as vocab
from acb_skills import company_search_tools as cst
from gateway.kb_entities import graph
from gateway.retrieval import find

_ROOT = Path(__file__).resolve().parents[2]
_CONSUMERS = (
    "apps/services/gateway/gateway/kb_entities/graph.py",
    "apps/services/gateway/gateway/retrieval/find.py",
    "packages/acb_skills/acb_skills/company_search_tools.py",
)
_NAMES = {"NODE_KINDS", "PROVENANCES", "RELATIONS", "APP_KINDS", "EDGES",
          "RECORD_EDGES", "MAIL_EDGES", "EDGE_NOUNS", "MAIL_WORDS"}


def _literal_definitions(path: str) -> list[str]:
    """The names of :data:`_NAMES` that *path* defines from a literal."""
    tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
    out: list[str] = []
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        value = getattr(node, "value", None)
        for t in targets:
            if isinstance(t, ast.Name) and t.id in _NAMES and value is not None \
                    and not isinstance(value, (ast.Attribute, ast.Name)):
                out.append(t.id)
    return out


@pytest.mark.parametrize("path", _CONSUMERS)
def test_no_consumer_defines_its_own_list(path: str) -> None:
    assert _literal_definitions(path) == []


def test_each_consumer_reads_the_one_object() -> None:
    assert graph.NODE_KINDS is vocab.NODE_KINDS
    assert graph.RELATIONS is vocab.RELATIONS
    assert graph.APP_KINDS is vocab.APP_KINDS
    assert find.EDGES is vocab.EDGES
    assert find.RECORD_EDGES is vocab.RECORD_EDGES
    assert find.MAIL_EDGES is vocab.MAIL_EDGES
    assert cst.EDGE_NOUNS is vocab.RECORD_WORDS
    assert cst.MAIL_WORDS is vocab.MAIL_WORDS


def test_the_live_codes_did_not_change() -> None:
    # FIND-3 answers byte for byte as before this slice.
    assert vocab.RECORD_EDGES == {"contact_of": "contact", "lead_of": "lead",
                                  "deal_of": "deal", "company_of": "company"}
    assert vocab.MAIL_EDGES == {"mail_from": "address", "mail_to": "address",
                                "mail_domain": "domain"}
    assert {c: vocab.RECORD_WORDS[c] for c in vocab.RECORD_EDGES} == {
        "contact_of": "a contact", "lead_of": "the lead", "deal_of": "a deal",
        "company_of": "the company"}
    assert vocab.MAIL_WORDS == {"mail_from": "mail from", "mail_to": "mail to",
                                "mail_domain": "mail from the domain of"}


def test_codes_are_unique_and_each_has_words() -> None:
    assert len(set(vocab.EDGES)) == len(vocab.CODES)
    assert all(c.words for c in vocab.CODES)
    assert set(vocab.EDGES) == set(vocab.RECORD_WORDS) | set(vocab.MAIL_WORDS)


def test_each_node_kind_has_exactly_one_record_code() -> None:
    kinds = [c.kind for c in vocab.CODES if c.key is None]
    assert sorted(kinds) == sorted(vocab.NODE_KINDS)
    for kind in vocab.NODE_KINDS:
        code = vocab.record_code(kind)
        assert code in vocab.RECORD_WORDS
    assert vocab.record_code("nothing") is None


def test_each_end_of_each_relation_has_a_code() -> None:
    # A stored edge reads in both directions, so a source is a neighbour too.
    for rel in vocab.RELATIONS.values():
        for kind in (*rel.sources, *rel.targets):
            assert vocab.record_code(kind) is not None, (rel.name, kind)


def test_every_live_code_has_a_lane() -> None:
    # `find.lanes` puts each candidate in the lane of its code. A live code with
    # no lane would raise KeyError there (verifier finding, CI-R1a).
    assert set(find.LANES) == set(vocab.RECORD_EDGES) | set(vocab.MAIL_EDGES)
