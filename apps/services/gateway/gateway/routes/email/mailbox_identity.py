"""The identity of a mailbox: its label and its colour slot (WS-17 EM-T8b).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.4 and D-EM-21.

A member can connect several mailboxes. Each surface that can show two of
them draws a chip: a dot of the categorical ramp and the label. Before EM-T8b
every Outlook mailbox got the label "Outlook" and the colour ``#6366f1``, so
two mailboxes looked the same (MB-8).

This module is the ONE place that decides a label and a slot. The accounts
API returns ``display_label`` and ``color_slot``, and the UI draws them. The
UI never derives a label of its own.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

#: The slots of the categorical ramp (``--cat-1`` to ``--cat-12``).
SLOTS = range(1, 13)

#: Domains that a person uses for private mail. A mailbox there is "Personal".
CONSUMER_DOMAINS = frozenset({
    "outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com",
    "outlook.in", "hotmail.co.uk", "live.in",
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.in", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "aol.com", "zoho.com", "gmx.com",
})

#: The labels that the connect wrote by itself, from the provider name. A
#: stored label equal to one of them counts as no label, because the member
#: did not choose it (§11.4).
PROVIDER_LABELS = frozenset({"outlook", "gmail", "email"})


def chosen_label(label: str | None, address: str | None = None) -> str | None:
    """The label the member chose, or ``None`` when the member chose none.

    A stored label equal to the address of the mailbox is no choice either:
    the IMAP form on Integrations writes the address when the name is blank
    (EM-T8b review).
    """
    text = (label or "").strip()
    if not text or text.lower() in PROVIDER_LABELS:
        return None
    if address and text.lower() == address.strip().lower():
        return None
    return text


def reserved_label(label: str) -> bool:
    """True for a name that the gateway keeps for the default label.

    A member who names a mailbox "Outlook" would see the default label
    instead, so the PATCH refuses the name with a reason (EM-T8b review).
    """
    return label.strip().lower() in PROVIDER_LABELS


def _title(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def _domain_label(address: str) -> str:
    local, _, domain = address.strip().lower().partition("@")
    if not domain:
        return _title(local) or "Mailbox"
    if domain in CONSUMER_DOMAINS:
        return "Personal"
    return _title(domain.split(".")[0])


def _local_label(address: str) -> str:
    local = address.strip().partition("@")[0]
    return _title(local.split("+")[0]) or address.strip()


def display_labels(mailboxes: Iterable[tuple[str, str, str | None]]) -> dict[str, str]:
    """The label of each mailbox of ONE member, keyed by mailbox id.

    ``mailboxes`` holds ``(id, address, stored_label)`` for every mailbox of
    the member in the organization, because a default label depends on the
    others. The rules of §11.4, in order:

    1. A label that the member chose wins.
    2. A consumer domain gives "Personal". Any other domain gives its first
       part, with a capital letter: ``vj@fracktal.in`` gives "Fracktal".
    3. When a default label equals another label of the member, chosen or
       default, each of those mailboxes takes its local part:
       ``sales@fracktal.in`` gives "Sales".
    4. When that is still not unique, the mailbox takes its address.

    Two labels that the member chose can be the same. That is a choice of
    the member, and the address still shows beside each chip.
    """
    rows = [(str(i), a or "", chosen_label(lbl, a)) for i, a, lbl in mailboxes]
    out = {i: chosen for i, _, chosen in rows if chosen is not None}
    chosen_keys = {v.lower() for v in out.values()}
    first = {i: _domain_label(a) for i, a, chosen in rows if chosen is None}
    clash = Counter(v.lower() for v in first.values())
    second: dict[str, str] = {}
    for i, a, chosen in rows:
        if chosen is not None:
            continue
        key = first[i].lower()
        if clash[key] > 1 or key in chosen_keys:
            second[i] = _local_label(a)
        else:
            out[i] = first[i]
    taken = Counter(v.lower() for v in out.values())
    clash2 = Counter(v.lower() for v in second.values())
    addresses = {i: a for i, a, _ in rows}
    for i, v in second.items():
        key = v.lower()
        out[i] = addresses[i] if clash2[key] > 1 or taken[key] else v
    return {i: out[i] for i, _, _ in rows}


def default_labels(mailboxes: Iterable[tuple[str, str, str | None]]) -> dict[str, str]:
    """The label each mailbox shows when the member clears its name.

    For each mailbox, :func:`display_labels` runs with the chosen label of
    THAT mailbox removed and the others kept. The rename dialog shows it as
    the placeholder and the preview of a blank name, so the UI never derives
    a label of its own (EM-T8b review).
    """
    rows = [(str(i), a or "", lbl) for i, a, lbl in mailboxes]
    return {
        i: display_labels((j, a, None if j == i else lbl) for j, a, lbl in rows)[i]
        for i, _, _ in rows
    }


def lowest_free_slot(used: Iterable[int | None]) -> int:
    """The lowest slot of 1 to 12 that no other mailbox of the member uses.

    With all twelve in use, the slots repeat in order (§11.6 edge case 6).
    """
    taken = Counter(s for s in used if s in SLOTS)
    for slot in SLOTS:
        if taken[slot] == 0:
            return slot
    least = min(taken[s] for s in SLOTS)
    return next(s for s in SLOTS if taken[s] == least)


def valid_slot(slot: object) -> bool:
    return isinstance(slot, int) and not isinstance(slot, bool) and slot in SLOTS



#: The slot of a NEW mailbox, as a scalar subquery inside its INSERT. It
#: applies :func:`lowest_free_slot` in SQL: the least-used slot of the other
#: mailboxes of the member in the organization, then the lowest. The INSERT
#: binds ``:member`` (the address of the member, any case) and ``:org``.
#: ``tests/unit/test_email_mailbox_identity.py`` checks that the two agree on
#: a real database (R8).
NEXT_SLOT_SQL = """(
    SELECT s.slot
      FROM generate_series(1, 12) AS s(slot)
      LEFT JOIN (
            SELECT color_slot, count(*) AS n
              FROM email_accounts
             WHERE lower(user_id) = lower(:member)
               AND organization_id = CAST(:org AS uuid)
               AND color_slot IS NOT NULL
             GROUP BY color_slot
      ) used ON used.color_slot = s.slot
     ORDER BY COALESCE(used.n, 0), s.slot
     LIMIT 1
)"""
