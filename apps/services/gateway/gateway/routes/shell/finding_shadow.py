"""The shadow measure of a new nudge kind (LOOP-9, ``proactive_loop.md`` §7.6).

A kind in ``shadow`` reaches no member (``finding_contract.deliver`` gives
nothing for it). ``finding_contract.shadowed`` lists what delivery WOULD have
sent, and the caller records each one as a :class:`Shown`. This module turns
those records and the acts of the members into the precision of the kind:
how often a finding matched an act of the member within 24 hours (§7.6, §7.7).

It is pure and does no I/O. It gives counts for each kind, never for a person
(§7.7). A count of fewer than 5 members shows as ``too_few``, and the gate
refuses a kind with ``too_few``. The gate number is the owner's: it is in the
declaration (``KindDeclaration.gate``). A kind with no number never passes.

The module does not flip a mode. ``on`` is the owner's flip (CLAUDE.md §3a).
Persisting the :class:`Shown` records needs a table, and it is a later slice.

Fence: ``tests/unit/test_finding_shadow.py``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gateway.routes.shell.finding_contract import Finding, KindDeclaration

#: The window of a match (§7.6): an act within 24 hours of the finding.
ACT_WINDOW = timedelta(hours=24)
#: A pooled measure needs this many members or more (§7.7).
MIN_MEMBERS = 5


@dataclass(frozen=True)
class Shown:
    """One finding that shadow kept from one member."""

    kind: str
    organization_id: str
    member: str
    entity_type: str
    entity_id: str
    at: datetime


@dataclass(frozen=True)
class Act:
    """One act of a member on one record, from the log of acts."""

    organization_id: str
    member: str
    entity_type: str
    entity_id: str
    at: datetime


def shown_of(finding: Finding, member: str, at: datetime) -> Shown:
    """The record of one shadowed finding. The entity is the typed id."""
    entity = finding.entity
    return Shown(kind=finding.kind, organization_id=finding.organization_id,
                 member=(member or "").strip().lower(),
                 entity_type=str(entity.get("type", "")), entity_id=str(entity.get("id", "")),
                 at=at)


def _matched(shown: Shown, acts: Mapping[tuple[str, str, str, str], list[datetime]]) -> bool:
    key = (shown.organization_id, shown.member, shown.entity_type, shown.entity_id)
    return any(shown.at <= when <= shown.at + ACT_WINDOW for when in acts.get(key, ()))


def precision(shown: Iterable[Shown], acts: Iterable[Act]) -> dict[str, dict[str, Any]]:
    """The precision of each kind, for each organization, in one pass.

    Keys are ``"<kind>"`` then ``organization_id``. Each entry has ``shown``,
    ``acted``, ``members``, ``too_few`` and ``precision`` (``None`` when
    ``too_few`` or when nothing was shown). An act counts only for the same
    organization and member, and only inside the window. No act is read across
    organizations, and no member is named in the answer.
    """
    index: dict[tuple[str, str, str, str], list[datetime]] = defaultdict(list)
    for a in acts:
        index[(a.organization_id, (a.member or "").strip().lower(),
               a.entity_type, a.entity_id)].append(a.at)
    counts: dict[tuple[str, str], list[Any]] = {}
    for s in shown:
        row = counts.setdefault((s.kind, s.organization_id), [0, 0, set()])
        row[0] += 1
        row[1] += 1 if _matched(s, index) else 0
        row[2].add(s.member)
    out: dict[str, dict[str, Any]] = {}
    for (kind, org), (n, hit, members) in sorted(counts.items()):
        few = len(members) < MIN_MEMBERS
        out.setdefault(kind, {})[org] = {
            "shown": n, "acted": hit, "members": len(members), "too_few": few,
            "precision": None if few or not n else round(hit / n, 4),
        }
    return out


def gate_passes(decl: KindDeclaration, measure: Mapping[str, Any]) -> bool:
    """Did the shadow week reach the owner's number? Refuses on every doubt.

    No number in the declaration, ``too_few``, or no precision all give False.
    A pass is a fact for the owner. It does not flip the mode.
    """
    if decl.gate is None:
        return False
    value = measure.get("precision")
    if measure.get("too_few") or not isinstance(value, (int, float)):
        return False
    return value >= decl.gate
