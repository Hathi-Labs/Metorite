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

**Two mailboxes PAIR when neither is separate (D-EM-30, WS-17 EM-T8g-3).**
:data:`PAIRED_MAILBOX_IDS_SQL` is the one rule of the pair set. "Also in"
(:func:`also_in_by_message`) and the draft dedupe (:func:`draft_skip_in_pair`)
read only that set. The self set above still keeps a separate mailbox.
Fence: ``tests/unit/test_email_duplicates.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from gateway.routes.email.core import IN_ALL_INBOXES_SQL, NOT_A_COPY_FOLDERS
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


#: A bind name such as ``:aid``, or a column of the caller's query such as
#: ``m.account_id``. Nothing else may anchor the FROM clause below.
_ANCHOR = re.compile(r":[a-z_]+|[a-z_]+\.[a-z_]+")


def _member_mailboxes_from(anchor: str) -> str:
    """The mailboxes of the member who owns the mailbox ``anchor``.

    ``a`` is that mailbox and ``o`` is each mailbox of the same member, ``a``
    included. Row level security binds the organization. The organization
    predicate repeats that bind for a session that row level security does
    not bind, so a mailbox of the same member in another organization is
    never ``o``. ``anchor`` is code, never a value from a request, and any
    other shape raises."""
    if not _ANCHOR.fullmatch(anchor):
        raise ValueError(f"not a bind name or a column: {anchor!r}")
    return (
        "FROM email_accounts a "
        "JOIN email_accounts o ON o.user_id = a.user_id "
        "AND o.organization_id IS NOT DISTINCT FROM a.organization_id "
        f"WHERE a.id = {anchor}")


#: The FROM clause over ``:aid``. Its text is the same as before EM-T8g-3.
_MEMBER_MAILBOXES_FROM = _member_mailboxes_from(":aid")

#: The lower-case address of each mailbox of the member who owns ``:aid``
#: (D-EM-27). A SQL caller puts it in its own text as a subquery, for example
#: ``LOWER(em.from_address->>'email') NOT IN (<this>)``, with ``:aid`` bound.
#: ``email_address`` is NOT NULL, so ``NOT IN`` never meets a NULL here.
SELF_ADDRESSES_SQL = f"SELECT LOWER(o.email_address) AS addr {_MEMBER_MAILBOXES_FROM}"

#: The id of each mailbox of the member who owns ``:aid``, ``:aid`` included.
SELF_MAILBOX_IDS_SQL = f"SELECT o.id {_MEMBER_MAILBOXES_FROM}"


def paired_mailbox_ids_sql(anchor: str = ":aid") -> str:
    """The id of each mailbox that PAIRS with the mailbox ``anchor``
    (WS-17 EM-T8g-3 item 4, D-EM-30).

    A paired mailbox is ANOTHER mailbox of the same member in the same
    organization, and neither of the two is separate. So the set leaves out
    each separate mailbox, and it is empty when ``anchor`` is separate.
    "Also in" and the draft dedupe read only this set. ``anchor`` is ``:aid``
    for one mailbox, or a column such as ``m.account_id`` for a page of mail.
    """
    return (f"SELECT o.id {_member_mailboxes_from(anchor)} "
            f"AND o.id <> a.id AND a.{IN_ALL_INBOXES_SQL} "
            f"AND o.{IN_ALL_INBOXES_SQL}")


#: The paired mailboxes of ``:aid``: the one rule of the pair set (EM-T8g-3).
#: The self set above keeps a separate mailbox, and this set does not.
PAIRED_MAILBOX_IDS_SQL = paired_mailbox_ids_sql(":aid")


def _not_a_copy_sql(alias: str) -> str:
    """The mail ``alias`` is a copy: it is outside drafts, junk and trash
    (``core.NOT_A_COPY_FOLDERS``, bound as ``:not_a_copy``)."""
    return f"LOWER(COALESCE({alias}.folder, '')) <> ALL(:not_a_copy)"


#: "Also in" for a page of mail (EM-T8g-3 item 1, §11.6 edge case 10). One
#: row for each mail of ``:ids`` and each paired mailbox that holds a copy of
#: it: a mail with the same ``internet_message_id``, not empty, outside drafts,
#: junk and trash. A mail with an empty id pairs with nothing. ``:uid`` keeps
#: the read to the mailboxes of the caller. The LATERAL pair set takes the
#: mailbox of each mail, and ``idx_email_messages_internet_message_id``
#: (migration 89, ``(account_id, internet_message_id)``) serves each lookup of
#: a copy as an index condition (measured on 40,000 rows after ANALYZE), so
#: the read needs no new index.
#: ⚠️ Only the Outlook provider stores ``internet_message_id`` today, so only
#: two Outlook mailboxes pair (the Known limit of EM-T8g-3).
ALSO_IN_SQL = f"""
    SELECT m.id::text AS id, p.id::text AS other
      FROM email_messages m
      CROSS JOIN LATERAL ({paired_mailbox_ids_sql("m.account_id")}) AS p (id)
     WHERE m.id = ANY(CAST(:ids AS uuid[]))
       AND m.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)
       AND COALESCE(m.internet_message_id, '') <> ''
       AND EXISTS (
             SELECT 1 FROM email_messages c
              WHERE c.account_id = p.id
                AND c.internet_message_id = m.internet_message_id
                AND {_not_a_copy_sql("c")})
     ORDER BY 1, 2"""


async def also_in_by_message(
    db: Any, message_ids: Iterable[Any], owner: str,
) -> dict[str, list[str]]:
    """``also_in`` of each mail of ONE page, in ONE read (EM-T8g-3 item 1).

    Maps a mail id to the ids of the paired mailboxes that hold a copy, in id
    order. A mail with no copy is absent, and the caller reads that as an
    empty list. An empty page runs no read. ``fetchall()`` of a real session
    gives a list. Any other answer, or a row that is not a mapping with these
    names (a hermetic test double), counts as no row."""
    ids = sorted({str(i) for i in message_ids if i})
    if not ids or not owner:
        return {}
    rows = (await db.execute(text(ALSO_IN_SQL), {
        "ids": ids, "uid": owner, "not_a_copy": list(NOT_A_COPY_FOLDERS),
    })).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows if isinstance(rows, list | tuple) else ():
        mid, other = getattr(r, "id", None), getattr(r, "other", None)
        if isinstance(mid, str) and isinstance(other, str):
            out.setdefault(mid, []).append(other)
    return out


#: The try-lock of an automatic draft (EM-T8g-3 item 5). The key is the
#: organization, the member and the Message-ID, so the two runs for the copies
#: of one mail in two paired mailboxes ask for ONE lock. The lock is
#: transaction-scoped: it holds until the block of the run commits, after the
#: provider draft and its local copy. No row means no pair: the Message-ID is
#: empty, the member has no paired mailbox, or ``:aid`` is separate. Then the
#: run takes no lock.
_DRAFT_LOCK_SQL = f"""
    SELECT pg_try_advisory_xact_lock(hashtextextended(
             'email-draft:' || COALESCE(CAST(box.organization_id AS text), '')
             || ':' || LOWER(box.user_id) || ':' || m.internet_message_id, 0)
           ) AS got
      FROM email_messages m
      JOIN email_accounts box ON box.id = m.account_id
     WHERE m.id = :mid AND m.account_id = :aid
       AND COALESCE(m.internet_message_id, '') <> ''
       AND EXISTS ({PAIRED_MAILBOX_IDS_SQL})"""

#: The question of the draft dedupe (EM-T8g-3 item 3, §11.6 edge case 11).
#: Does a paired mailbox hold a copy of ``:mid`` whose thread already holds a
#: draft, or a sent mail newer than the copy? A copy with no thread id has no
#: thread to read. Measured on 40,000 rows after ANALYZE: migration 89's index
#: serves the copy and ``idx_email_messages_thread`` (migration 17) serves
#: the thread read.
_ANSWERED_IN_PAIR_SQL = f"""
    SELECT EXISTS (
      SELECT 1
        FROM email_messages m
        JOIN email_messages c ON c.internet_message_id = m.internet_message_id
       WHERE m.id = :mid AND m.account_id = :aid
         AND COALESCE(m.internet_message_id, '') <> ''
         AND c.account_id IN ({PAIRED_MAILBOX_IDS_SQL})
         AND {_not_a_copy_sql("c")}
         AND EXISTS (
               SELECT 1 FROM email_messages t
                WHERE t.account_id = c.account_id
                  AND t.thread_id = c.thread_id
                  AND (LOWER(COALESCE(t.folder, '')) = 'drafts'
                       OR (LOWER(COALESCE(t.folder, '')) = 'sent'
                           AND t.received_at > c.received_at)))
    ) AS answered"""


async def draft_skip_in_pair(
    db: Any, account_id: str, message_id: str,
) -> str | None:
    """Why the automatic draft of ``message_id`` in ``account_id`` must not
    start, or None to draft (EM-T8g-3 items 3 and 5).

    - ``"busy"``: another run holds the try-lock on the member and the
      Message-ID. Two overlapping runs for one mail make one draft at most.
    - ``"answered"``: a paired mailbox holds a copy, and the thread of that
      copy holds a draft or a sent mail newer than the copy.

    Two statements, on purpose. READ COMMITTED takes a new snapshot for each
    statement, so the check after the lock sees each draft that a run
    committed before it released the lock. One statement would read a
    snapshot from before the lock. A mail with no pair takes no lock and
    reads nothing more. A row that is not a real row (a hermetic test double)
    counts as no answer, so the draft goes on as before."""
    if not account_id or not message_id:
        return None
    params = {"aid": account_id, "mid": message_id}
    lock = (await db.execute(text(_DRAFT_LOCK_SQL), params)).fetchone()
    if lock is None:
        return None
    if getattr(lock, "got", None) is False:
        return "busy"
    row = (await db.execute(text(_ANSWERED_IN_PAIR_SQL), {
        **params, "not_a_copy": list(NOT_A_COPY_FOLDERS)})).fetchone()
    return "answered" if getattr(row, "answered", None) is True else None


def recipient_lists_sql(alias: str) -> str:
    """The To, Cc and Bcc lists of the mail ``alias`` as one JSON array.

    A list that is NULL or not an array counts as empty, so one NULL list
    never makes the whole array NULL. The Sent-copy proof and the rule of a
    thread with only the member's mailboxes both read recipients here."""
    return (
        f"CASE WHEN jsonb_typeof({alias}.to_addresses) = 'array' "
        f"THEN {alias}.to_addresses ELSE '[]'::jsonb END "
        f"|| CASE WHEN jsonb_typeof({alias}.cc_addresses) = 'array' "
        f"THEN {alias}.cc_addresses ELSE '[]'::jsonb END "
        f"|| CASE WHEN jsonb_typeof({alias}.bcc_addresses) = 'array' "
        f"THEN {alias}.bcc_addresses ELSE '[]'::jsonb END")

#: A Sent copy proves that the member sent the mail ``:mid`` of the mailbox
#: ``:aid`` from ANOTHER of their mailboxes (EM-T8e-1 review rounds 1 and 2).
#: Four tests, and each one refuses a forgery:
#:
#: - the same ``internet_message_id``, which is not empty
#: - the ``sent`` folder: a forged mail to both mailboxes puts a copy with the
#:   same Message-ID in the INBOX of the other mailbox
#: - a mailbox of the member that is not ``:aid``
#: - the copy names the address of ``:aid`` in To, Cc or Bcc: an outsider who
#:   got a real mail of B can replay its Message-ID to A, and B's Sent copy of
#:   that mail names the outsider, not A (round 2)
#:
#: ``idx_email_messages_internet_message_id`` (migration 89) serves the join.
#: ⚠️ Only the Outlook provider stores ``internet_message_id`` today, so the
#: proof exists only between two Outlook mailboxes (§11.6 edge case 26).
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
       AND EXISTS (
             SELECT 1 FROM jsonb_array_elements({recipient_lists_sql("s")}) AS r(addr)
              WHERE LOWER(r.addr->>'email') = (
                    SELECT LOWER(ea.email_address) FROM email_accounts ea
                     WHERE ea.id = m.account_id))
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
