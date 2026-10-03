"""Automation · sender identity & direction.

The single primitive both the rule classifier and the sender categorizer use to
decide whether a message's sender is the mailbox owner (``self``), the owner's
ORGANISATION (``internal`` — same email domain, or a configured extra domain), or
``external``.

Why it exists: classification looks at one email in isolation, so an OUTBOUND
business document — e.g. an invoice your sales team sent a customer — reads like a
RECEIVED ``Receipt``. Knowing the sender is you / your org lets the classifier
refuse the receive-only categories (Receipt / Newsletter / Marketing / Cold
Email) for your own outbound/internal mail and treat it as FYI instead.

``extra_domains`` carries the OPTIONAL ``org_domains`` setting (extra domains
beyond the account's own); when none are configured, detection is same-domain
only and needs no setup.

**Self covers each mailbox of the member (D-EM-27, WS-17 EM-T8e-1).** A member
can connect several mailboxes. "Self" is any address of a mailbox of the member
in this organization, so mail between two of them is not external.
:data:`SELF_ADDRESSES_SQL` and :func:`resolve_self` are the one SQL helper for
that set. The internal domain stays the domain of the current mailbox.
Fence: ``tests/unit/test_email_ai_context.py`` (``email-self-each-mailbox``).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from gateway.routes.email.mailbox_identity import display_labels
from sqlalchemy import text


def _domain_of(addr: str) -> str:
    addr = (addr or "").strip().lower()
    return addr.rsplit("@", 1)[1] if "@" in addr else ""


def normalize_domain(raw: str) -> str:
    """A user-entered domain → bare lowercase host: strips a leading ``@``, an
    ``email@`` local-part, a trailing path, and surrounding whitespace. '' if
    empty."""
    d = (raw or "").strip().lower().lstrip("@")
    if "@" in d:  # someone pasted a full address
        d = d.rsplit("@", 1)[1]
    return d.split("/")[0].strip()  # drop any trailing path


async def resolve_org_domains(db: Any, account_id: str) -> frozenset[str]:
    """The account's configured EXTRA org domains (``email_assistant_settings.
    org_domains``), normalized. The account's own domain is always internal via
    ``sender_scope`` regardless, so this is purely additive. Returns an empty set
    on any error / no config / pre-migration (column absent), so callers degrade
    to same-domain detection."""
    try:
        row = (await db.execute(text(
            "SELECT org_domains FROM email_assistant_settings "
            "WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
    except Exception:  # noqa: BLE001 — column may not exist yet / DB hiccup
        return frozenset()
    vals = getattr(row, "org_domains", None) if row else None
    if not vals:
        return frozenset()
    try:
        return frozenset(
            nd for v in vals
            if isinstance(v, str) and (nd := normalize_domain(v)))
    except TypeError:  # vals not iterable (e.g. a mock) → no config
        return frozenset()


#: The mailboxes of the member who owns the mailbox ``:aid``. ``a`` is that
#: mailbox and ``o`` is each mailbox of the same member, ``a`` included.
#: Row level security binds the organization. The organization predicate
#: repeats that bind for a session that row level security does not bind, so
#: a mailbox of the same member in another organization is never ``o``.
_MEMBER_MAILBOXES_FROM = (
    "FROM email_accounts a "
    "JOIN email_accounts o ON o.user_id = a.user_id "
    "AND o.organization_id IS NOT DISTINCT FROM a.organization_id "
    "WHERE a.id = :aid")

#: The lower-case address of each mailbox of the member who owns ``:aid``
#: (D-EM-27). A SQL caller puts it in its own text as a subquery, for example
#: ``LOWER(em.from_address->>'email') NOT IN (<this>)``, with ``:aid`` bound.
#: ``email_address`` is NOT NULL, so ``NOT IN`` never meets a NULL here.
SELF_ADDRESSES_SQL = f"SELECT LOWER(o.email_address) AS addr {_MEMBER_MAILBOXES_FROM}"

#: The id of each mailbox of the member who owns ``:aid``, ``:aid`` included.
SELF_MAILBOX_IDS_SQL = f"SELECT o.id {_MEMBER_MAILBOXES_FROM}"

#: A Sent copy proves that the member sent the mail ``:mid`` of the mailbox
#: ``:aid`` from ANOTHER of their mailboxes (EM-T8e-1 review round 1). The copy
#: has the same ``internet_message_id`` and sits in the ``sent`` folder of a
#: mailbox of the member that is not ``:aid``. A forged From has no such copy.
#: ``idx_email_messages_internet_message_id`` (migration 89) serves the join.
_PROVEN_OWN_SEND_SQL = f"""
    SELECT 1
      FROM email_messages m
      JOIN email_messages s
        ON s.internet_message_id = m.internet_message_id
     WHERE m.id = :mid AND m.account_id = :aid
       AND COALESCE(m.internet_message_id, '') <> ''
       AND s.account_id <> m.account_id
       AND LOWER(COALESCE(s.folder, '')) = 'sent'
       AND s.account_id IN ({SELF_MAILBOX_IDS_SQL})
     LIMIT 1"""

#: The same mailboxes as rows, for :func:`resolve_self`. One FROM clause, so
#: the set in SQL and the set in Python cannot disagree.
_MEMBER_MAILBOXES_SQL = (
    "SELECT o.id::text AS id, o.email_address AS address, o.label AS label "
    f"{_MEMBER_MAILBOXES_FROM} ORDER BY o.created_at, o.id")


@dataclass(frozen=True)
class SelfIdentity:
    """Who the mailbox ``account_id`` speaks as, and who "self" is.

    ``address`` and ``label`` name the mailbox itself, for the drafter
    (MB-14). ``self_addresses`` holds the lower-case address of each mailbox
    of the member, this one included (D-EM-27). Each field is empty when the
    mailbox row is not visible.
    """

    address: str = ""
    label: str = ""
    self_addresses: frozenset[str] = frozenset()


async def resolve_self(db: Any, account_id: str) -> SelfIdentity:
    """The identity of the mailbox ``account_id``, in one read.

    The label is the display label of ``mailbox_identity.display_labels``,
    the one place that decides a label. The read uses the session of the
    caller, so the tenant bind of that session applies. A row that is not a
    mapping with these names (a hermetic test double) counts as no row.
    """
    rows = (await db.execute(
        text(_MEMBER_MAILBOXES_SQL), {"aid": account_id})).fetchall()
    boxes: list[tuple[str, str, str | None]] = []
    for r in rows or []:
        box_id, address = getattr(r, "id", None), getattr(r, "address", None)
        if isinstance(box_id, str) and isinstance(address, str) and address:
            label = getattr(r, "label", None)
            boxes.append((box_id, address, label if isinstance(label, str) else None))
    if not boxes:
        return SelfIdentity()
    labels = display_labels(boxes)
    want = str(account_id or "").strip().lower()
    own = next(((i, a) for i, a, _ in boxes if i.lower() == want), ("", ""))
    return SelfIdentity(
        address=own[1], label=labels.get(own[0], "") if own[0] else "",
        self_addresses=frozenset(a.strip().lower() for _, a, _ in boxes))


async def resolve_self_addresses(db: Any, account_id: str) -> frozenset[str]:
    """The lower-case address of each mailbox of the member who owns
    ``account_id`` (D-EM-27). Empty when the mailbox row is not visible."""
    return (await resolve_self(db, account_id)).self_addresses


async def proven_own_send(db: Any, account_id: str, message_id: str) -> bool:
    """True when a Sent copy in another mailbox of the member proves that the
    member sent the mail ``message_id`` of ``account_id``.

    The From header alone is not proof, because an outside sender can forge
    it. The cold check skips a mail only with this proof (EM-T8e-1 review
    round 1). Without a Sent copy, for example before the other mailbox
    syncs, the answer is False and the cold check runs as it did before."""
    if not account_id or not message_id:
        return False
    row = (await db.execute(text(_PROVEN_OWN_SEND_SQL),
                            {"aid": account_id, "mid": message_id})).fetchone()
    return row is not None


def own_addresses(
    self_email: str, self_addresses: Iterable[str] = (),
) -> frozenset[str]:
    """``self_email`` and ``self_addresses``, lower case, with no blank entry.

    A caller that has one address still gets the right answer, because the
    set then holds that address only."""
    found = {(a or "").strip().lower() for a in (self_addresses or ())}
    found.add((self_email or "").strip().lower())
    found.discard("")
    return frozenset(found)


def sender_scope(
    from_email: str,
    self_email: str,
    extra_domains: frozenset[str] | set[str] = frozenset(),
    *,
    self_addresses: Iterable[str] = (),
) -> str:
    """Classify a sender's provenance relative to the mailbox owner.

    Returns one of:
    - ``"self"``     — the owner's own address sent it (you personally), or the
      address of another mailbox of the owner (``self_addresses``, D-EM-27).
    - ``"internal"`` — same email domain as the owner, or one of ``extra_domains``
      (your organisation, but not you).
    - ``"external"`` — anyone else.

    Fails SAFE to ``"external"`` on empty/garbage input, so we never suppress a
    genuine receive-category for mail we couldn't identify. ``self_email`` with no
    domain (shouldn't happen) yields same-domain matching off the empty string,
    i.e. only an exact ``self`` match counts.

    ``self_addresses`` is keyword-only and optional, so a caller with ONE
    address (``routes/crm/auto_lead.py``) keeps working unchanged. The
    internal domain stays the domain of ``self_email``, the current mailbox.
    """
    frm = (from_email or "").strip().lower()
    me = (self_email or "").strip().lower()
    if not frm or "@" not in frm:
        return "external"
    if frm in own_addresses(me, self_addresses):
        return "self"
    own_domain = _domain_of(me)
    domains = {d for d in ({own_domain} | {
        (e or "").strip().lower().lstrip("@") for e in extra_domains}) if d}
    return "internal" if (_domain_of(frm) in domains) else "external"


def is_own_mail(
    from_email: str,
    self_email: str,
    extra_domains: frozenset[str] | set[str] = frozenset(),
    *,
    self_addresses: Iterable[str] = (),
) -> bool:
    """True when the sender is you or your organisation (``self``/``internal``) —
    i.e. NOT external mail you received from an outside party."""
    return sender_scope(from_email, self_email, extra_domains,
                        self_addresses=self_addresses) != "external"
