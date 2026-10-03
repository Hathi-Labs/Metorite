"""Transport · contacts — the person card behind a sender's name/avatar.

``GET /email/contacts/card`` answers one question the reading pane keeps
raising: *who is this?* Clicking a display name or avatar in the mail app pops
a card with the address, what we know about them, how the correspondence has
gone, and their last few messages — the Outlook people-card affordance.

There is no contacts table and no directory to read from: everything here is
derived from mail already in ``email_messages`` for THIS user's accounts, plus
the sender rollups the automation layer maintains (``email_senders`` category,
``email_newsletters`` disposition).

Contact details (phone, title, organisation, links) are parsed out of the
sender's own signature block on their most recent messages. That parse is
deterministic and read-only — nothing is persisted, and the numbers are shown
only to the owner of the mailbox the message was addressed to.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.email.core import _account_scope, _tenant_session, _log, router
from pydantic import BaseModel
from sqlalchemy import text

# How many recent messages to read bodies from when hunting for a signature.
# The card only ever shows one contact block, so the first message that yields
# anything wins; a handful is enough to skip a couple of one-line replies.
_SIGNATURE_SCAN_MESSAGES = 6

# Only the tail of a message can plausibly be a signature. Bounded so a long
# newsletter body never gets regex-scanned in full.
_SIGNATURE_TAIL_CHARS = 1200

# Free mail hosts — the domain says nothing about where the person works, so
# don't offer it as their organisation.
_GENERIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com",
    "mail.com", "zoho.com", "fastmail.com", "hey.com", "qq.com", "163.com",
})

# Hyphen, en dash and em dash — signature blocks use all three interchangeably
# as separators, so every place that strips or matches one must accept all.
# Written as escapes so the dashes stay distinguishable in source.
_DASHES = "-\u2013\u2014"
# Bullets a signature line may be decorated or joined with.
_BULLETS = "\u00b7\u2022"

# A labelled phone line ("Mobile: +91 98765 43210", "T +1 415 555 0132"). The
# label is what makes this safe — an unlabelled digit run in a message body is
# far more often an order number, an invoice total or a date.
_LABELLED_PHONE = re.compile(
    rf"(?:^|[\s|{_BULLETS}(\[])"
    r"(?:tel|telephone|phone|ph|mobile|mob|cell|direct|office|work|fax|whatsapp|"
    r"m|t|p|o|d|f)"
    rf"\s*(?:\.|:|\)|[{_DASHES}])?\s*"
    # The number itself may open with a country code, an area code in
    # parentheses ("(415) 555-0132"), or a bare digit.
    r"(\+?\(?\d[\d\s().\-]{6,20}\d)",
    re.IGNORECASE | re.MULTILINE,
)

# An international number stands on its own: a leading + and a country code is
# an unambiguous phone signal even with no label in front of it.
_E164_PHONE = re.compile(r"\+\d[\d\s().\-]{6,20}\d")

# Signatures write their site both ways — "https://acme.com" and a bare
# "www.acme.com". Requiring the www. on the bare form keeps stray words with a
# dot in them (and email domains) out.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]\"']+", re.IGNORECASE)

# Links that are plumbing, not the person's own site.
_LINK_NOISE = re.compile(
    r"unsubscribe|opt[-_]?out|mailchimp|list-manage|sendgrid|hubspot|"
    r"click\.|track|utm_|/preferences|/privacy|/terms|calendly\.com/[^/]+/?$",
    re.IGNORECASE,
)

# Where a signature starts, when the sender marks it.
_SIG_SEPARATOR = re.compile(r"^\s*(--\s*|__+|—+|-{2,})\s*$", re.MULTILINE)

# Quoted-reply banners — everything from here down belongs to someone else, so
# a signature hunt must stop before it (otherwise the "signature" found is the
# person being replied TO).
_QUOTE_BANNER = re.compile(
    r"^\s*(?:>|On .{0,80}wrote:|-{2,}\s*Original Message|From:\s|Sent from my)",
    re.IGNORECASE | re.MULTILINE,
)

# Lines that are never a job title or a company name.
_NOT_A_TITLE = re.compile(
    r"@|https?://|www\.|\+\d|confidential|disclaimer|sent from|unsubscribe|"
    r"^\s*[\d\W]+\s*$",
    re.IGNORECASE,
)


class ContactRecentMessage(BaseModel):
    """One of the person's recent messages, as a card in the popover."""

    id: str
    thread_id: str | None = None
    account_id: str
    subject: str
    """Body preview, already truncated server-side for the card."""
    preview: str
    received_at: str | None = None
    is_read: bool = True
    has_attachments: bool = False
    folder: str = ""


class ContactDetails(BaseModel):
    """What the sender's own signature says about them. All fields optional —
    an empty details block is the normal case for a personal address."""

    phones: list[str] = []
    title: str | None = None
    organization: str | None = None
    links: list[str] = []
    """Message the signature was read from, so the UI can cite it."""
    source_message_id: str | None = None


class ContactStats(BaseModel):
    received: int = 0
    """Messages the user sent where this person was a To/Cc recipient."""
    sent: int = 0
    unread: int = 0
    threads: int = 0
    first_seen: str | None = None
    last_seen: str | None = None


class ContactCardModel(BaseModel):
    email: str
    name: str | None = None
    domain: str | None = None
    """Sender rollup category (Newsletter / Conversation / …), when assigned."""
    category: str | None = None
    """Cleaner disposition: APPROVED | UNSUBSCRIBED | AUTO_ARCHIVED | UNHANDLED."""
    status: str = "UNHANDLED"
    stats: ContactStats = ContactStats()
    details: ContactDetails = ContactDetails()
    recent: list[ContactRecentMessage] = []


def _normalize_phone(raw: str) -> str | None:
    """Tidy a matched phone and reject the things that only look like one."""
    cleaned = re.sub(r"[^\d+]", "", raw)
    plus = cleaned.startswith("+")
    digits = cleaned.lstrip("+")
    if not digits.isdigit():
        return None
    # Below 8 digits is a room number or an extension; above 15 breaks E.164 and
    # is almost always an id that happened to carry separators.
    if not (8 <= len(digits) <= 15):
        return None
    # A bare 8-digit run reads as a date (20260727) far more often than a phone;
    # require either a country code or a longer national number.
    if not plus and len(digits) < 9:
        return None
    display = re.sub(r"\s{2,}", " ", raw.strip(f" .|{_DASHES}{_BULLETS}")).strip()
    if plus and not display.startswith("+"):
        display = f"+{display}"
    return display


def _signature_block(body: str) -> str:
    """The slice of a message that could be the sender's own signature.

    Cut at the first quoted-reply banner (below it is another person's mail),
    then prefer the text after the last ``--`` separator; failing that, take the
    tail. Returns '' when there's nothing plausible left.
    """
    if not body:
        return ""
    quote = _QUOTE_BANNER.search(body)
    own = body[: quote.start()] if quote else body
    own = own.rstrip()
    if not own:
        return ""
    seps = list(_SIG_SEPARATOR.finditer(own))
    if seps:
        block = own[seps[-1].end():]
        if block.strip():
            return block[:_SIGNATURE_TAIL_CHARS]
    return own[-_SIGNATURE_TAIL_CHARS:]


def _parse_phones(block: str) -> list[str]:
    """Labelled numbers first (the strongest signal), then bare E.164 ones."""
    candidates = [m.group(1) for m in _LABELLED_PHONE.finditer(block)]
    candidates += [m.group(0) for m in _E164_PHONE.finditer(block)]
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        phone = _normalize_phone(raw)
        if not phone:
            continue
        # De-dupe on the national part: "+91 98765 43210" and "9876543210" in
        # the same block are one number written twice.
        key = re.sub(r"\D", "", phone)[-10:]
        if key in seen:
            continue
        seen.add(key)
        out.append(phone)
        if len(out) == 3:
            break
    return out


def _parse_links(block: str) -> list[str]:
    out: list[str] = []
    for match in _URL.finditer(block):
        url = match.group(0).rstrip(".,;:)")
        if _LINK_NOISE.search(url):
            continue
        # Serve something the UI can put straight in an href.
        if url.lower().startswith("www."):
            url = f"https://{url}"
        if url in out:
            continue
        out.append(url)
        if len(out) == 3:
            break
    return out


def _parse_title_and_org(block: str, name: str | None) -> tuple[str | None, str | None]:
    """Title / company from the lines that follow the sender's name.

    Signature blocks are overwhelmingly ``Name`` → ``Title`` → ``Company``. Find
    the name line and read the next couple of short, plain lines. Without a name
    to anchor on there is no reliable signal, so return nothing rather than
    guess at an arbitrary line.
    """
    if not name or not name.strip():
        return None, None
    lines = [ln.strip(f" \t*|{_BULLETS}{_DASHES}") for ln in block.splitlines()]
    first = (name.strip().split()[0] or "").lower()
    last = (name.strip().split()[-1] or "").lower()
    anchor = -1
    for i, line in enumerate(lines):
        low = line.lower()
        if line and len(line) <= 60 and first in low and last in low:
            anchor = i
            break
    if anchor < 0:
        return None, None
    picked: list[str] = []
    for line in lines[anchor + 1: anchor + 6]:
        if not line:
            continue
        if len(line) > 60 or _NOT_A_TITLE.search(line):
            continue
        picked.append(line)
        if len(picked) == 2:
            break
    title = picked[0] if picked else None
    org = picked[1] if len(picked) > 1 else None
    return title, org


def _org_from_domain(domain: str | None) -> str | None:
    """A corporate domain names the company; a free mail host names nothing."""
    if not domain or domain in _GENERIC_DOMAINS:
        return None
    label = domain.split(".")[0]
    if len(label) < 2 or label in {"mail", "email", "smtp", "info", "no-reply"}:
        return None
    return label.replace("-", " ").title()


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _preview(row: Any, max_chars: int = 180) -> str:
    """Short plain-text preview for a recent-message card."""
    raw = (getattr(row, "snippet", None) or getattr(row, "body_text", None) or "")
    collapsed = re.sub(r"\s+", " ", raw).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars].rstrip() + "…"


# One clause per column the signature writer may set. Each keeps a hand-edited
# value (the column is listed in manual_fields) and otherwise takes the newly
# parsed one — but only when the parse actually found something, so a reply with
# no sign-off never blanks what an earlier signature taught us.
_KEEP_MANUAL = """
    {col} = CASE
        WHEN '{col}' = ANY(email_contacts.manual_fields) THEN email_contacts.{col}
        ELSE COALESCE(EXCLUDED.{col}, email_contacts.{col})
    END"""
_KEEP_MANUAL_ARRAY = """
    {col} = CASE
        WHEN '{col}' = ANY(email_contacts.manual_fields) THEN email_contacts.{col}
        WHEN cardinality(EXCLUDED.{col}) > 0 THEN EXCLUDED.{col}
        ELSE email_contacts.{col}
    END"""

_REMEMBER_CONTACT_SQL = f"""
INSERT INTO email_contacts
    (account_id, email, display_name, title, organization, phones, links,
     source, source_message_id, parsed_at)
VALUES
    (:account_id, :email, :display_name, :title, :organization, :phones, :links,
     'signature', :source_message_id, now())
ON CONFLICT (account_id, email) DO UPDATE SET
    {_KEEP_MANUAL.format(col='display_name')},
    {_KEEP_MANUAL.format(col='title')},
    {_KEEP_MANUAL.format(col='organization')},
    {_KEEP_MANUAL_ARRAY.format(col='phones')},
    {_KEEP_MANUAL_ARRAY.format(col='links')},
    -- A row a human has touched is theirs, whatever wrote it last.
    source = CASE WHEN cardinality(email_contacts.manual_fields) > 0
                  THEN 'user' ELSE 'signature' END,
    source_message_id = COALESCE(EXCLUDED.source_message_id,
                                 email_contacts.source_message_id),
    parsed_at = now(),
    updated_at = now()
RETURNING display_name, title, organization, phones, links, source_message_id
"""


async def _remember_contact(
    db: Any,
    account_id: str,
    email: str,
    name: str | None,
    details: ContactDetails,
) -> Any:
    """Persist what this card learned into the ``email_contacts`` directory.

    The card derives contact details on every open, which answers one question
    and leaves nothing behind. Writing them makes the mailbox build a people
    directory as a side effect of being read — the store a Contacts view, a
    people picker, or a "who do we know at this company" query needs, none of
    which can re-scan every message body on demand.

    Never overwrites a column listed in ``manual_fields``: a human correction
    outranks anything a signature says, permanently. Best-effort — a failure
    here must never cost the caller their card.

    Returns the MERGED row so the card can show exactly what the directory now
    holds (a hand-edited title, or a phone an older signature taught us that
    today's one-line reply doesn't repeat). Returns None if the write failed.
    """
    try:
        row = (await db.execute(text(_REMEMBER_CONTACT_SQL), {
            "account_id": account_id,
            "email": email,
            "display_name": name,
            "title": details.title,
            "organization": details.organization,
            "phones": details.phones,
            "links": details.links,
            "source_message_id": details.source_message_id,
        })).fetchone()
        # No commit here: the only caller is the converted contact_card (H2),
        # whose `_tenant_session` commits on clean exit — a mid-block commit
        # would end that transaction and drop the tenant GUC.
        return row
    except Exception:
        _log.debug("contact directory upsert failed for %s", email, exc_info=True)
        return None


@router.get("/contacts/card", response_model=ContactCardModel)
async def contact_card(
    email: str = Query(..., description="The person's email address"),
    account_id: str | None = Query(
        None, description="Scope to one mailbox; omit to span the user's accounts"),
    limit: int = Query(3, ge=1, le=10, description="How many recent messages"),
    name: str | None = Query(
        None, description="Display name the caller already has — used when no "
                          "mail was ever RECEIVED from this address (a "
                          "recipient you only ever write to)"),
    user: UserContext = Depends(get_current_user),
) -> ContactCardModel:
    """Everything the mail app knows about one person, for the hover/click card.

    Scoped to the caller's own accounts throughout — an address is only ever
    resolved against mail that already belongs to this user.

    Side effect by design: what the signature parse learns is written into the
    ``email_contacts`` directory (see _remember_contact), so reading a card is
    also how the mailbox builds its people store.
    """
    address = (email or "").strip().lower()
    domain = address.split("@")[-1] if "@" in address else None
    card = ContactCardModel(email=address, domain=domain)
    if not address:
        return card

    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous", "addr": address}
        scope = _account_scope(account_id, params)
        # Mail FROM this person. Drafts are excluded: an unsent draft of the
        # user's own is not correspondence from them, and a provider that echoes
        # the address would otherwise pad every count.
        from_them = (
            f"{scope} AND LOWER(em.from_address->>'email') = :addr "
            "AND LOWER(COALESCE(em.folder, '')) <> 'drafts'"
        )

        stats_row = (await db.execute(text(
            f"""SELECT COUNT(*) AS received,
                       COUNT(*) FILTER (WHERE em.is_read = false) AS unread,
                       COUNT(DISTINCT COALESCE(em.thread_id, em.id::text)) AS threads,
                       MIN(em.received_at) AS first_seen,
                       MAX(em.received_at) AS last_seen,
                       (ARRAY_AGG(em.from_address->>'name'
                                  ORDER BY em.received_at DESC)
                        FILTER (WHERE COALESCE(em.from_address->>'name', '') <> ''))[1]
                           AS display_name
                  FROM email_messages em
                 WHERE {from_them}"""
        ), params)).fetchone()

        # Mail the user sent this person — the other half of the relationship,
        # and the number that says whether this is a correspondent or a list.
        sent_row = (await db.execute(text(
            f"""SELECT COUNT(*) AS sent
                  FROM email_messages em
                 WHERE {scope}
                   AND LOWER(COALESCE(em.folder, '')) = 'sent'
                   AND EXISTS (
                       SELECT 1 FROM jsonb_array_elements(
                           COALESCE(em.to_addresses, '[]'::jsonb)
                           || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r
                        WHERE LOWER(r->>'email') = :addr)"""
        ), params)).fetchone()

        card.stats = ContactStats(
            received=int(getattr(stats_row, "received", 0) or 0),
            sent=int(getattr(sent_row, "sent", 0) or 0),
            unread=int(getattr(stats_row, "unread", 0) or 0),
            threads=int(getattr(stats_row, "threads", 0) or 0),
            first_seen=_iso(getattr(stats_row, "first_seen", None)),
            last_seen=_iso(getattr(stats_row, "last_seen", None)),
        )
        # The name they sign their mail with, else the one the caller already
        # had (a recipient you only ever write TO has no from_address to read).
        card.name = (
            (getattr(stats_row, "display_name", None) or "").strip()
            or (name or "").strip()
            or None
        )

        # Sender rollups: the category the classifier assigned, and whether the
        # Email Cleaner has a disposition on file.
        rollup = (await db.execute(text(
            """SELECT (SELECT category FROM email_senders se
                        WHERE se.account_id IN (
                              SELECT id FROM email_accounts WHERE user_id = :uid)
                          AND LOWER(se.email) = :addr
                        ORDER BY se.updated_at DESC NULLS LAST LIMIT 1) AS category,
                      (SELECT status FROM email_newsletters ne
                        WHERE ne.account_id IN (
                              SELECT id FROM email_accounts WHERE user_id = :uid)
                          AND LOWER(ne.email) = :addr
                        ORDER BY ne.updated_at DESC NULLS LAST LIMIT 1) AS status"""
        ), {"uid": params["uid"], "addr": address})).fetchone()
        if rollup is not None:
            card.category = getattr(rollup, "category", None) or None
            card.status = getattr(rollup, "status", None) or "UNHANDLED"

        # Recent messages — the newest first, one row per message (a burst in
        # one thread is still several messages from them, and the card is about
        # the person, not the conversation).
        scan = max(limit, _SIGNATURE_SCAN_MESSAGES)
        rows = (await db.execute(text(
            f"""SELECT em.id, em.thread_id, em.account_id, em.subject,
                       em.snippet, em.body_text, em.received_at, em.is_read,
                       em.has_attachments, em.folder
                  FROM email_messages em
                 WHERE {from_them}
                 ORDER BY em.received_at DESC
                 LIMIT :scan"""
        ), {**params, "scan": scan})).fetchall()

        card.recent = [
            ContactRecentMessage(
                id=str(r.id),
                thread_id=r.thread_id,
                account_id=str(r.account_id),
                subject=(r.subject or "(no subject)"),
                preview=_preview(r),
                received_at=_iso(r.received_at),
                is_read=bool(r.is_read),
                has_attachments=bool(r.has_attachments),
                folder=r.folder or "",
            )
            for r in rows[:limit]
        ]

        # Signature parse — first message that yields anything wins.
        details = ContactDetails()
        for r in rows[:_SIGNATURE_SCAN_MESSAGES]:
            block = _signature_block(r.body_text or "")
            if not block:
                continue
            phones = _parse_phones(block)
            links = _parse_links(block)
            title, org = _parse_title_and_org(block, card.name)
            if not (phones or links or title or org):
                continue
            details = ContactDetails(
                phones=phones, links=links, title=title, organization=org,
                source_message_id=str(r.id),
            )
            break

        # Remember what we learned, and show what the directory now holds — the
        # merged row, so a hand-edited title or a phone from an older signature
        # survives a reply that carried no sign-off at all.
        owner = account_id or (str(rows[0].account_id) if rows else None)
        if owner and (card.name or details.phones or details.links
                      or details.title or details.organization):
            merged = await _remember_contact(db, owner, address, card.name, details)
            if merged is not None:
                card.name = (merged.display_name or "").strip() or card.name
                details = ContactDetails(
                    phones=list(merged.phones or []),
                    links=list(merged.links or []),
                    title=merged.title,
                    organization=merged.organization,
                    source_message_id=(
                        str(merged.source_message_id)
                        if merged.source_message_id else None
                    ),
                )

        # The domain is a last-resort company name, never stored — it is a guess
        # about the address, not something the person told us.
        if not details.organization:
            details.organization = _org_from_domain(domain)
        card.details = details

        return card


# ── Recipient autocomplete ────────────────────────────────────────────────────


class ContactSuggestion(BaseModel):
    email: str
    name: str = ""


#: The most addresses one ``/contacts/sent-from`` read takes (EM-T8c).
SENT_FROM_MAX = 20


@router.get("/contacts/sent-from")
async def sent_from(
    emails: str = Query(..., description="Comma-separated addresses, at most 20"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """For each address, the mailbox of the member that last WROTE to it.

    WS-17 EM-T8c (``email_app_master_plan.md`` §11.4): the From row warns
    "You usually write to this person from <label>" when the member picks
    another mailbox. The answer maps a lower-case address to a mailbox id of
    the member. An address that no mailbox wrote to is absent. The owner
    predicate is in the SQL, so a mailbox of another member never answers.
    Best-effort: a failure answers ``{}``, because a warning must never stop
    the composer.
    """
    wanted = sorted({
        e.strip().lower() for e in (emails or "").split(",")
        if "@" in e and len(e.strip()) <= 254
    })[:SENT_FROM_MAX]
    if not wanted:
        return {}
    try:
        async with _tenant_session() as db:
            rows = (await db.execute(text(
                """
                SELECT DISTINCT ON (a.addr) a.addr, em.account_id
                  FROM email_messages em
                  JOIN email_accounts ea ON ea.id = em.account_id
                 CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(em.to_addresses, '[]'::jsonb)
                        || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r
                 CROSS JOIN LATERAL (SELECT LOWER(r->>'email') AS addr) AS a
                 WHERE ea.user_id = :uid
                   AND LOWER(COALESCE(em.folder, '')) = 'sent'
                   AND a.addr = ANY(:wanted)
                 ORDER BY a.addr, em.received_at DESC NULLS LAST
                """
            ), {"uid": user.email or "anonymous", "wanted": wanted})).fetchall()
        return {r.addr: str(r.account_id) for r in rows}
    except Exception as exc:  # a warning is best-effort
        _log.warning("email.sent_from_failed", error=str(exc)[:160])
        return {}


@router.get("/contacts/suggest")
async def suggest_contacts(
    q: str = Query(..., min_length=1, description="Prefix/substring to match"),
    account_id: str | None = Query(
        None, description="Scope to one mailbox; omit to span the user's accounts"),
    limit: int = Query(8, ge=1, le=20),
    user: UserContext = Depends(get_current_user),
) -> list[ContactSuggestion]:
    """People-picker autocomplete for the composers' To/Cc/Bcc fields.

    Candidates, strongest first: addresses the user has SENT mail to (the
    autofill signal that matters most), the learned ``email_contacts``
    directory, and every known sender. Matched on address OR display name;
    prefix matches rank above substring hits, then by how often the address
    appears and how recently. The user's own connected addresses are excluded.
    Best-effort: any failure returns [] — a typeahead must never error the
    composer."""
    needle = (q or "").strip().lower()
    if not needle:
        return []
    try:
        async with _tenant_session() as db:
            params: dict[str, Any] = {
                "uid": user.email or "anonymous",
                "aid": account_id or "",
                "any": f"%{needle}%",
                "pre": f"{needle}%",
                "lim": limit,
            }
            rows = (await db.execute(text(
                """
                WITH accts AS (
                    SELECT id FROM email_accounts
                     WHERE user_id = :uid AND (:aid = '' OR id::text = :aid)
                ),
                cands (email, name, weight, last_at) AS (
                    -- People the user has WRITTEN to — the strongest signal.
                    SELECT LOWER(r->>'email'), NULLIF(TRIM(r->>'name'), ''),
                           3, em.received_at
                      FROM email_messages em
                      CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(em.to_addresses, '[]'::jsonb)
                        || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r
                     WHERE em.account_id IN (SELECT id FROM accts)
                       AND LOWER(COALESCE(em.folder, '')) = 'sent'
                    UNION ALL
                    -- The learned people directory (names parsed from signatures).
                    SELECT LOWER(ec.email), NULLIF(TRIM(ec.display_name), ''),
                           2, ec.updated_at
                      FROM email_contacts ec
                     WHERE ec.account_id IN (SELECT id FROM accts)
                    UNION ALL
                    -- Everyone who has mailed the user (the senders rollup).
                    SELECT LOWER(es.email), NULLIF(TRIM(es.name), ''),
                           1, es.updated_at
                      FROM email_senders es
                     WHERE es.account_id IN (SELECT id FROM accts)
                )
                SELECT c.email,
                       COALESCE(MAX(c.name), '') AS name,
                       (BOOL_OR(c.email LIKE :pre)
                        OR BOOL_OR(COALESCE(c.name, '') ILIKE :pre)) AS prefix_hit,
                       MAX(c.weight)  AS weight,
                       COUNT(*)       AS hits,
                       MAX(c.last_at) AS last_at
                  FROM cands c
                 WHERE c.email IS NOT NULL AND POSITION('@' IN c.email) > 1
                   AND (c.email LIKE :any OR COALESCE(c.name, '') ILIKE :any)
                   AND c.email NOT IN (
                       SELECT LOWER(email_address) FROM email_accounts
                        WHERE user_id = :uid)
                 GROUP BY c.email
                 ORDER BY prefix_hit DESC, weight DESC, hits DESC,
                          last_at DESC NULLS LAST
                 LIMIT :lim
                """
            ), params)).fetchall()
            return [ContactSuggestion(email=r.email, name=r.name or "")
                    for r in rows]
    except Exception as exc:  # noqa: BLE001 — typeahead is best-effort
        _log.warning("email.contact_suggest_failed", error=str(exc)[:160])
        return []
