"""The finding contract of the proactive loop (LOOP-7).

Spec: ``project-docs/specs/proactive_loop.md`` §6 and §8.4, with
``project-docs/specs/package_host.md`` §4 (the ``nudges`` and ``insights``
points) and ``my_day_feed.md`` §4.1 (the signal).

This module holds three things and does no I/O, apart from one read of the
member's groups:

* **The declaration of one kind** (§6.2) and its check.
* **The fields of one finding** (§6.1) and its check (§6.4 rules 1 and 2).
* **The delivery check** (§8.4). ``visibility_scope`` is checked again for each
  recipient when the finding is delivered, not only when it is found. A
  recipient that the scope does not hold gets nothing, and gets no error.

Words never come from a model here (§6.4 rule 4). A finding carries typed
facts and a template id, and no prose.

Fences: ``tests/unit/test_finding_contract.py`` (the contract and the
fixtures) and ``tests/unit/test_finding_contract_r8.py`` (the delivery check
against real group rows).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The two points of ``package_host.md`` §4 that a kind joins.
POINTS: frozenset[str] = frozenset({"nudges", "insights"})

#: The old-path groups of ``my_day_feed.md`` §4.3 and the ``severity_hint``
#: values. A nudge asks the member to act. An insight is for information.
NUDGE_GROUPS: frozenset[str] = frozenset({"now", "needs_you", "ready"})
INSIGHT_GROUPS: frozenset[str] = frozenset({"info"})
SEVERITIES: frozenset[str] = NUDGE_GROUPS | INSIGHT_GROUPS

#: The most a kind may do outside the app, from least to most. ``none`` is the
#: default, so a kind with no reviewed class never leaves the app (§6.4 rule 5).
REACH_CLASSES: tuple[str, ...] = ("none", "brief", "interrupt", "urgent")
HARM_CLASSES: frozenset[str] = frozenset({"normal", "high"})

PRIOR_MIN, PRIOR_MAX = -20, 20
EVIDENCE_MIN, EVIDENCE_MAX = 1, 3
DEFAULT_LOWER_AFTER = 3

_EVIDENCE_KEYS = ("app", "type", "id", "label", "href")
#: A D12 subject: ``org``, ``group:<slug>`` or a lower-case address.
_SUBJECT = re.compile(r"^(org|group:[a-z0-9][a-z0-9_-]*|[^@\s:]+@[^@\s:]+)$")


@dataclass(frozen=True)
class Evidence:
    """One record behind a finding, the shape of ``related`` in
    ``proactive_reach.md`` §4.5."""

    app: str
    type: str
    id: str
    label: str
    href: str


@dataclass(frozen=True)
class Finding:
    """One finding. The first eight fields are the signal of
    ``my_day_feed.md`` §4.1. The last five are new (§6.1)."""

    kind: str
    entity: Mapping[str, Any]
    signal_id: str
    fingerprint: str
    facts: Mapping[str, Any]
    expires_at: str | None
    acts: Sequence[Mapping[str, Any]]
    href: str
    evidence: Sequence[Evidence] = ()
    confidence: float = 1.0
    visibility_scope: Sequence[str] = ()
    severity_hint: str = "info"
    batch_key: str | None = None


@dataclass(frozen=True)
class KindDeclaration:
    """The declaration of one kind, made once in a manifest point (§6.2).

    ``entity_types`` is the set of entity types the kind may name. It lets the
    declaration check refuse a person as the subject of a fault (ALN-Q5).
    """

    kind: str
    label: str
    point: str
    group: str
    why: Sequence[str]
    source: str
    entity_types: Sequence[str]
    fixtures: Mapping[str, Any] = field(default_factory=dict)
    reach_class: str = "none"
    harm_class: str = "normal"
    prior: int = 0
    lower_after: int = DEFAULT_LOWER_AFTER


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def reach_rank(reach_class: str) -> int:
    return REACH_CLASSES.index(reach_class)


def check_declaration(decl: KindDeclaration) -> list[str]:
    """The faults of a declaration, as plain sentences. Empty means it holds."""
    out: list[str] = []
    for name in ("kind", "label", "source"):
        if not _text(getattr(decl, name)):
            out.append(f"{name} is empty")
    if decl.point not in POINTS:
        out.append(f"point {decl.point!r} is not nudges or insights")
    allowed = INSIGHT_GROUPS if decl.point == "insights" else NUDGE_GROUPS
    if decl.point in POINTS and decl.group not in allowed:
        out.append(f"group {decl.group!r} does not fit the {decl.point} point")
    if decl.reach_class not in REACH_CLASSES:
        out.append(f"reach_class {decl.reach_class!r} is unknown")
    elif decl.point == "insights" and reach_rank(decl.reach_class) > reach_rank("brief"):
        out.append("an insights kind has a reach class of brief at most")
    if decl.harm_class not in HARM_CLASSES:
        out.append(f"harm_class {decl.harm_class!r} is unknown")
    if (isinstance(decl.prior, bool) or not isinstance(decl.prior, int)
            or not PRIOR_MIN <= decl.prior <= PRIOR_MAX):
        out.append(f"prior must be an integer from {PRIOR_MIN} to {PRIOR_MAX}")
    if (isinstance(decl.lower_after, bool) or not isinstance(decl.lower_after, int)
            or decl.lower_after < 1):
        out.append("lower_after must be an integer of 1 or more")
    if not decl.why or not all(_text(w) for w in decl.why):
        out.append("why needs at least one template id")
    # ALN-Q5: no kind has a person as the subject of a fault.
    if not decl.entity_types:
        out.append("entity_types is empty")
    elif "person" in decl.entity_types:
        out.append("a kind may not name a person as its entity (ALN-Q5)")
    fixtures = decl.fixtures or {}
    for key in ("positive", "negative"):
        if key not in fixtures:
            out.append(f"the {key} fixture is missing")
    return out


def check_scope(scope: Iterable[str] | None) -> list[str]:
    values = list(scope or [])
    if not values:
        return ["visibility_scope is required and holds no subject"]
    bad = [s for s in values if not isinstance(s, str) or not _SUBJECT.match(s)]
    return [f"visibility_scope has a bad subject: {s!r}" for s in bad]


def check_finding(finding: Finding, decl: KindDeclaration | None = None) -> list[str]:
    """The faults of a finding (§6.1 and §6.4 rules 1 and 2).

    A finding with a fault drops before the feed. With *decl* given, the check
    also holds the finding to its kind.
    """
    out: list[str] = []
    if not _text(finding.kind):
        out.append("kind is empty")
    if not _text(finding.signal_id):
        out.append("signal_id is empty")
    if not _text(finding.fingerprint):
        out.append("fingerprint is empty")
    entity = finding.entity or {}
    if not all(_text(entity.get(k)) for k in ("type", "id", "label")):
        out.append("entity needs type, id and label")
    if entity.get("type") == "person":
        out.append("a person is never the entity of a finding (ALN-Q5)")
    count = len(finding.evidence or ())
    if not EVIDENCE_MIN <= count <= EVIDENCE_MAX:
        out.append(f"evidence needs {EVIDENCE_MIN} to {EVIDENCE_MAX} records, has {count}")
    for ev in finding.evidence or ():
        if not all(_text(getattr(ev, k, None)) for k in _EVIDENCE_KEYS):
            out.append("an evidence record needs app, type, id, label and href")
            break
    out.extend(check_scope(finding.visibility_scope))
    conf = finding.confidence
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        out.append("confidence must be a number from 0 to 1")
    if finding.severity_hint not in SEVERITIES:
        out.append(f"severity_hint {finding.severity_hint!r} is unknown")
    if not _text(finding.href):
        out.append("href is empty")
    if decl is not None:
        if finding.kind != decl.kind:
            out.append(f"kind {finding.kind!r} is not the declared kind {decl.kind!r}")
        if entity.get("type") not in set(decl.entity_types):
            out.append(f"entity type {entity.get('type')!r} is not declared by the kind")
        allowed = INSIGHT_GROUPS if decl.point == "insights" else NUDGE_GROUPS
        if finding.severity_hint not in allowed:
            out.append(f"severity_hint {finding.severity_hint!r} does not fit the {decl.point} point")
    return out


def check_fixtures(decl: KindDeclaration) -> list[str]:
    """The positive fixture passes the contract. The negative one fails it.

    A fixture is a ``Finding``. A kind with no fixture fails here (§6.5).
    """
    out: list[str] = []
    fixtures = decl.fixtures or {}
    pos, neg = fixtures.get("positive"), fixtures.get("negative")
    if not isinstance(pos, Finding):
        out.append("the positive fixture is not a Finding")
    elif faults := check_finding(pos, decl):
        out.append(f"the positive fixture fails: {faults[0]}")
    if not isinstance(neg, Finding):
        out.append("the negative fixture is not a Finding")
    elif not check_finding(neg, decl):
        out.append("the negative fixture passes, so it tests nothing")
    return out


# ── Delivery (§8.4) ─────────────────────────────────────────────────────────


def scope_holds(scope: Iterable[str], email: str, groups: Iterable[str]) -> bool:
    """True when a D12 subject of *scope* names the recipient.

    ``org`` holds each member of the tenant. ``group:<slug>`` holds a member of
    that group. An address holds that member. An empty scope holds nobody, so
    a finding fails closed. The comparison ignores case, as R10 says.
    """
    me = (email or "").strip().lower()
    if not me:
        return False
    mine = {g.lower() for g in groups}
    for subject in scope or ():
        s = str(subject).strip().lower()
        if s == "org" or s == me or (s.startswith("group:") and s in mine):
            return True
    return False


def deliverable(finding: Finding, email: str, groups: Iterable[str],
                decl: KindDeclaration | None = None) -> bool:
    """May this finding reach this recipient now? A failure is silent (§8.4)."""
    return not check_finding(finding, decl) and scope_holds(
        finding.visibility_scope, email, groups)


def deliver(findings: Iterable[Finding], email: str, groups: Iterable[str],
            registry: Mapping[str, KindDeclaration] | None = None) -> list[Finding]:
    """The findings that reach one recipient. The rest drop with no error.

    With a *registry*, a finding of a kind that nobody declared drops too.
    """
    mine = list(groups)
    out: list[Finding] = []
    for f in findings:
        decl = None
        if registry is not None:
            decl = registry.get(f.kind)
            if decl is None:
                continue
        if deliverable(f, email, mine, decl):
            out.append(f)
    return out


async def member_groups(db: Any, email: str) -> list[str]:
    """The ``group:<slug>`` subjects of one member, from the one groups query.

    It reuses ``routes/projects/core._MY_GROUPS_SQL`` and so counts only an
    active member. *db* is a tenant session from the ``get_db()`` seam, so RLS
    keeps the answer inside the tenant of the request.
    """
    from gateway.routes.projects.core import _MY_GROUPS_SQL
    from sqlalchemy import text

    rows = await db.execute(text(_MY_GROUPS_SQL), {"email": (email or "").strip().lower()})
    return [str(r[0]) for r in rows.fetchall()]


#: The kinds that apps have declared. LOOP-8 and the apps add to it. It is empty
#: at LOOP-7, and ``test_finding_contract.py`` runs each entry's fixtures.
REGISTRY: dict[str, KindDeclaration] = {}


def register(decl: KindDeclaration) -> None:
    """Add a kind. A bad declaration or a duplicate id fails at once."""
    faults = check_declaration(decl) or check_fixtures(decl)
    if faults:
        raise ValueError(f"kind {decl.kind!r}: {faults[0]}")
    if decl.kind in REGISTRY:
        raise ValueError(f"kind {decl.kind!r} is already declared")
    REGISTRY[decl.kind] = decl
