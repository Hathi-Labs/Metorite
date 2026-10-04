"""IMAP/SMTP email provider.

Uses standard IMAP (imaplib) for receiving and SMTP (smtplib) for sending.
Supports any IMAP-capable email server: Gmail app passwords, Microsoft 365
basic auth, WP Mail SMTP, Brevo SMTP relay, self-hosted, etc.

Credentials JSON shape for generic IMAP accounts:
  {
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_username": "user@example.com",
    "imap_password": "...",
    "imap_use_ssl": true,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "user@example.com",
    "smtp_password": "...",
    "smtp_use_starttls": true
  }
"""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import re
import smtplib
from datetime import datetime, timezone
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from typing import Any

from .base import (
    Attachment,
    BaseEmailProvider,
    EmailAddress,
    EmailFolder,
    EmailMessage,
    SyncResult,
    canonical_folder,
    find_unsubscribe_link_in_html,
)


class IMAPProvider(BaseEmailProvider):
    """IMAP + SMTP email provider for generic email accounts."""

    def __init__(self, credentials: dict[str, Any]):
        super().__init__(credentials)
        self._imap: imaplib.IMAP4 | None = None

    # -- IMAP connection management ------------------------------------------

    async def _get_imap(self) -> imaplib.IMAP4:
        if self._imap is not None:
            try:
                self._imap.noop()
                return self._imap
            except Exception:
                self._imap = None
        self._imap = await self._connect_imap()
        return self._imap

    async def _connect_imap(self) -> imaplib.IMAP4:
        host = self.credentials.get("imap_host", "")
        port = int(self.credentials.get("imap_port", 993))
        username = self.credentials.get("imap_username", "")
        password = self.credentials.get("imap_password", "")
        use_ssl = self.credentials.get("imap_use_ssl", True)

        if not host or not username or not password:
            raise ValueError(
                "Missing IMAP creds: imap_host, imap_username, imap_password"
            )

        def _connect() -> imaplib.IMAP4:
            if use_ssl:
                conn = imaplib.IMAP4_SSL(host, port, timeout=30)
            else:
                conn = imaplib.IMAP4(host, port, timeout=30)
            conn.login(username, password)
            return conn

        return await asyncio.to_thread(_connect)

    # -- Provider interface --------------------------------------------------

    async def authenticate(self) -> bool:
        try:
            conn = await self._connect_imap()
            conn.logout()
            return True
        except Exception:
            return False

    async def list_folders(self) -> list[EmailFolder]:
        imap = await self._get_imap()

        def _list() -> list[EmailFolder]:
            status, data = imap.list()
            if status != "OK":
                return []

            system_names = {
                "INBOX", "SENT", "DRAFTS", "TRASH", "SPAM", "JUNK",
                "ARCHIVE", "SENT MAIL", "SENT ITEMS",
                "DELETED ITEMS", "DELETED MESSAGES", "DRAFT",
            }

            folders: list[EmailFolder] = []
            for line in data:
                if not line:
                    continue
                decoded = (
                    line.decode("utf-8", errors="replace")
                    if isinstance(line, bytes)
                    else str(line)
                )
                parts = decoded.split('"')
                if len(parts) >= 4:
                    name = parts[3]
                elif len(parts) >= 2:
                    name = parts[-2].strip()
                else:
                    continue

                if not name:
                    continue

                short_name = name.split("/")[-1] if "/" in name else name
                ftype = "system" if short_name.upper().strip() in system_names else "user"

                msg_count = 0
                unread_count = 0
                try:
                    s, d = imap.status(f'"{name}"', "(MESSAGES UNSEEN)")
                    if s == "OK" and d and d[0]:
                        status_str = (
                            d[0].decode("utf-8", errors="replace")
                            if isinstance(d[0], bytes)
                            else str(d[0])
                        )
                        mm = re.search(r"MESSAGES\s+(\d+)", status_str)
                        if mm:
                            msg_count = int(mm.group(1))
                        um = re.search(r"UNSEEN\s+(\d+)", status_str)
                        if um:
                            unread_count = int(um.group(1))
                except Exception:
                    pass

                folders.append(
                    EmailFolder(
                        provider_folder_id=name,
                        name=short_name or name,
                        type=ftype,
                        message_count=msg_count,
                        unread_count=unread_count,
                    )
                )
            return folders

        return await asyncio.to_thread(_list)

    async def list_messages(
        self,
        folder: str = "INBOX",
        query: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        canonical_override: str | None = None,
    ) -> tuple[list[EmailMessage], str | None]:
        imap = await self._get_imap()

        # IMAP per-message folder is inferred from flags (INBOX/DRAFTS/TRASH);
        # for any other mailbox we stamp the canonical key of the selected folder
        # so the message is filed under the folder the user actually opened.
        canon = canonical_override or canonical_folder(folder)

        def _list() -> tuple[list[EmailMessage], str | None]:
            s, _ = imap.select(f'"{folder}"', readonly=True)
            if s != "OK":
                return [], None

            search_criteria = "ALL"
            if query:
                if query in ("is:unread", "is:unread in:inbox"):
                    search_criteria = "UNSEEN"
                elif query != "is:read":
                    search_criteria = f'TEXT "{query}"'

            s, data = imap.uid("SEARCH", None, search_criteria)
            if s != "OK" or not data or not data[0]:
                return [], None

            uids = data[0].split()[-max_results:]
            if not uids:
                return [], None

            uid_range = ",".join(
                uid.decode() if isinstance(uid, bytes) else str(uid) for uid in uids
            )
            s, msg_data = imap.uid(
                "FETCH",
                uid_range,
                "(FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.500> INTERNALDATE UID)",
            )
            if s != "OK" or not msg_data:
                return [], None

            messages = _parse_imap_fetch(msg_data)
            messages.reverse()
            # Stamp the canonical folder for non-INBOX mailboxes so Sent/Drafts/
            # user folders file correctly (the flag-based default is INBOX).
            if canon != "inbox":
                for m in messages:
                    m.folder = canon
            return messages, None

        return await asyncio.to_thread(_list)

    async def get_message(self, provider_message_id: str) -> EmailMessage:
        imap = await self._get_imap()

        def _get() -> EmailMessage:
            s, _ = imap.select("INBOX", readonly=True)
            if s != "OK":
                raise ValueError("Cannot select INBOX")

            s, data = imap.uid(
                "FETCH", provider_message_id, "(FLAGS BODY[] INTERNALDATE UID)"
            )
            if s != "OK" or not data:
                raise ValueError(f"Message not found: {provider_message_id}")

            messages = _parse_imap_fetch_full(data)
            if not messages:
                raise ValueError(f"Message not found: {provider_message_id}")
            return messages[0]

        return await asyncio.to_thread(_get)

    async def send_message(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to_message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        thread_id: str | None = None,
    ) -> str:
        smtp_host = self.credentials.get("smtp_host", "")
        smtp_port = int(self.credentials.get("smtp_port", 587))
        smtp_username = self.credentials.get(
            "smtp_username", self.credentials.get("imap_username", "")
        )
        smtp_password = self.credentials.get(
            "smtp_password", self.credentials.get("imap_password", "")
        )
        use_starttls = self.credentials.get("smtp_use_starttls", True)

        if not smtp_host:
            raise ValueError("Missing SMTP configuration: smtp_host")

        msg: Any
        if attachments:
            from email import encoders  # noqa: PLC0415
            from email.mime.base import MIMEBase  # noqa: PLC0415
            from email.mime.multipart import MIMEMultipart  # noqa: PLC0415
            msg = MIMEMultipart()
            msg.attach(MIMEText(body_text, "plain" if not body_html else "html"))
            for att in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att.get("content") or b"")
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{att.get("filename", "attachment")}"',
                )
                msg.attach(part)
        else:
            msg = MIMEText(body_text, "plain" if not body_html else "html")
        msg["From"] = smtp_username
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)
        # Thread the reply via In-Reply-To/References. For IMAP a message's
        # ``thread_id`` is its own RFC Message-ID, so the reply target's
        # thread_id is exactly the header value we need to reference.
        ref = reply_to_message_id or thread_id
        if ref:
            msg["In-Reply-To"] = ref
            msg["References"] = ref

        def _send() -> str:
            if use_starttls and smtp_port == 587:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)

            server.login(smtp_username, smtp_password)
            server.send_message(msg)
            server.quit()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            return f"smtp-{ts}-{hash(subject) & 0xFFFF:04x}"

        return await asyncio.to_thread(_send)

    async def create_draft(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> str:
        """Save a draft by APPENDing it to the Drafts mailbox with the \\Draft flag."""
        username = self.credentials.get(
            "imap_username", self.credentials.get("smtp_username", "")
        )
        msg = MIMEText(body_text, "plain" if not body_html else "html")
        msg["From"] = username
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(a for a in cc if a)
        if bcc:
            msg["Bcc"] = ", ".join(a for a in bcc if a)
        msg["Subject"] = subject
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        imap = await self._get_imap()

        def _append() -> str:
            import imaplib  # noqa: PLC0415
            import time  # noqa: PLC0415
            stamp = imaplib.Time2Internaldate(time.time())
            for folder in ("Drafts", "INBOX.Drafts", "[Gmail]/Drafts"):
                try:
                    typ, _ = imap.append(
                        folder, "(\\Draft)", stamp, msg.as_bytes()
                    )
                    if typ == "OK":
                        return f"draft-{folder}"
                except Exception:  # noqa: BLE001
                    continue
            raise RuntimeError("No Drafts mailbox available for APPEND")

        return await asyncio.to_thread(_append)

    async def modify_message(
        self,
        provider_message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        imap = await self._get_imap()

        def _modify() -> None:
            imap.select("INBOX", readonly=False)

            if add_labels:
                flags: list[str] = []
                for label in add_labels:
                    lu = label.upper()
                    if lu in ("READ", "SEEN"):
                        flags.append("\\Seen")
                    if lu in ("STARRED", "FLAGGED", "IMPORTANT"):
                        flags.append("\\Flagged")
                if flags:
                    imap.uid("STORE", provider_message_id, "+FLAGS", " ".join(flags))

            if remove_labels:
                flags = []
                for label in remove_labels:
                    if label.upper() == "UNREAD":
                        flags.append("\\Seen")
                if flags:
                    imap.uid("STORE", provider_message_id, "-FLAGS", " ".join(flags))

        await asyncio.to_thread(_modify)

    async def trash_message(self, provider_message_id: str) -> None:
        imap = await self._get_imap()

        def _trash() -> None:
            imap.select("INBOX", readonly=False)
            trash_dir = None
            for name in (
                "Trash", "TRASH", "Deleted Items", "Deleted Messages",
                "[Gmail]/Trash",
            ):
                try:
                    s, _ = imap.select(f'"{name}"', readonly=True)
                    if s == "OK":
                        trash_dir = name
                        break
                except Exception:
                    continue

            if trash_dir:
                imap.select(f'"{trash_dir}"', readonly=False)
                imap.uid("COPY", provider_message_id, trash_dir)

            imap.select("INBOX", readonly=False)
            imap.uid("STORE", provider_message_id, "+FLAGS", "\\Deleted")
            imap.expunge()

        await asyncio.to_thread(_trash)

    async def sync_messages(
        self,
        history_id: str | None = None,
        max_results: int = 100,
        deep: bool = False,
        since: datetime | None = None,
        catch_up: datetime | None = None,
        *,
        delta_shadow: bool = False,
    ) -> SyncResult:
        """Incremental sync using IMAP UIDNEXT/UIDVALIDITY.

        history_id format: "last_uid:uidvalidity".  ``deep``/``since``/
        ``catch_up``/``delta_shadow`` are accepted for interface parity but
        unused (IMAP is UID-incremental only).
        """
        imap = await self._get_imap()

        def _sync() -> SyncResult:
            s, _ = imap.select("INBOX", readonly=True)
            if s != "OK":
                return SyncResult(errors=["Cannot select INBOX"])

            s, data = imap.status("INBOX", "(UIDNEXT UIDVALIDITY)")
            if s != "OK" or not data or not data[0]:
                return SyncResult(errors=["Cannot get mailbox status"])

            status_str = (
                data[0].decode("utf-8", errors="replace")
                if isinstance(data[0], bytes)
                else str(data[0])
            )
            uidnext_m = re.search(r"UIDNEXT\s+(\d+)", status_str)
            uidvalidity_m = re.search(r"UIDVALIDITY\s+(\d+)", status_str)
            uidnext = int(uidnext_m.group(1)) if uidnext_m else 0
            uidvalidity = int(uidvalidity_m.group(1)) if uidvalidity_m else 0

            last_uid = 0
            stored_validity = None
            if history_id and ":" in history_id:
                parts = history_id.split(":", 1)
                last_uid = int(parts[0]) if parts[0].isdigit() else 0
                stored_validity = int(parts[1]) if parts[1].isdigit() else None

            new_history_id = f"{max(uidnext - 1, last_uid)}:{uidvalidity}"

            if stored_validity is not None and stored_validity != uidvalidity:
                last_uid = 0  # force full resync

            range_str = f"{last_uid + 1}:*" if last_uid > 0 else "1:*"
            s, search_data = imap.uid("SEARCH", None, range_str)
            if s != "OK" or not search_data or not search_data[0]:
                return SyncResult(new_history_id=new_history_id)

            uids = search_data[0].split()[-max_results:]
            if not uids:
                return SyncResult(new_history_id=new_history_id)

            uid_range = ",".join(
                uid.decode() if isinstance(uid, bytes) else str(uid) for uid in uids
            )
            s, msg_data = imap.uid(
                "FETCH", uid_range, "(FLAGS BODY[] INTERNALDATE UID)"
            )
            if s != "OK" or not msg_data:
                return SyncResult(errors=["FETCH failed"], new_history_id=new_history_id)

            messages = _parse_imap_fetch_full(msg_data)
            return SyncResult(
                messages_synced=len(messages),
                messages=messages,
                new_history_id=new_history_id,
            )

        # NOTE: IMAP sync is INBOX-only.  IMAP UIDs are unique only *within* a
        # mailbox (they restart per folder), but email_messages enforces a global
        # UNIQUE(account_id, provider_message_id) and get_message/apply_flags/
        # move all treat the id as a bare INBOX UID.  Syncing other mailboxes
        # would let e.g. a Sent UID collide with an Inbox UID and clobber it.
        # Multi-folder IMAP needs folder-namespaced ids end-to-end first (see the
        # email review notes); until then user folders stay empty for IMAP.
        return await asyncio.to_thread(_sync)

    async def get_attachment(
        self, provider_message_id: str, provider_attachment_id: str
    ) -> bytes:
        imap = await self._get_imap()

        def _get_att() -> bytes:
            imap.select("INBOX", readonly=True)
            s, data = imap.uid(
                "FETCH",
                provider_message_id,
                f"(BODY[{provider_attachment_id}])",
            )
            if s != "OK" or not data or not data[0]:
                raise ValueError(f"Attachment not found: {provider_attachment_id}")

            for item in data:
                if isinstance(item, tuple):
                    return item[1]
            raise ValueError("No attachment data in response")

        return await asyncio.to_thread(_get_att)


# -- IMAP message parsing helpers ----------------------------------------------


def _decode_imap_header(value: str) -> str:
    """Decode RFC 2047 encoded header values."""
    if not value:
        return ""
    try:
        parts = decode_header(value)
        result = ""
        for part, charset in parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="replace")
            else:
                result += str(part)
        return result
    except Exception:
        return value


def _parse_imap_address(raw: str) -> EmailAddress | None:
    """Parse an email address from an IMAP header value."""
    if not raw:
        return None
    decoded = _decode_imap_header(raw)
    if "<" in decoded and ">" in decoded:
        name = decoded.split("<")[0].strip().strip('"')
        email = decoded.split("<")[1].split(">")[0].strip()
        return EmailAddress(name=name, email=email)
    return EmailAddress(name="", email=decoded.strip())


def _parse_imap_address_list(raw: str) -> list[EmailAddress]:
    """Parse a comma-separated list of email addresses."""
    if not raw:
        return []
    decoded = _decode_imap_header(raw)
    addresses: list[EmailAddress] = []
    for part in _split_imap_addresses(decoded):
        addr = _parse_imap_address(part.strip())
        if addr and addr.email:
            addresses.append(addr)
    return addresses


def _split_imap_addresses(raw: str) -> list[str]:
    """Split comma-separated addresses respecting quoted names."""
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    for ch in raw:
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
        elif ch == "," and not in_quote:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _parse_imap_internaldate(raw_date: str) -> datetime | None:
    """Parse IMAP INTERNALDATE format into a timezone-aware datetime."""
    if not raw_date:
        return None
    try:
        # INTERNALDATE format: "17-Jun-2026 14:30:00 +0000"
        raw_str = raw_date.strip().strip('"')
        dt = parsedate_to_datetime(raw_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _parse_imap_fetch(msg_data: list) -> list[EmailMessage]:
    """Parse IMAP FETCH response (headers + text preview + flags + internaldate).

    Used by list_messages() for fast listing without full body.
    """
    messages: list[EmailMessage] = []
    current: dict[str, Any] = {}

    for item in msg_data:
        if item is None:
            continue
        if isinstance(item, bytes):
            continue
        if isinstance(item, tuple):
            # Extract UID from the fetch response
            response_str = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
            uid_match = re.search(r"UID\s+(\d+)", response_str)
            uid = uid_match.group(1) if uid_match else ""

            flags_match = re.search(r"FLAGS\s+\(([^)]*)\)", response_str)
            flags = flags_match.group(1).split() if flags_match else []

            internaldate_match = response_str.split("INTERNALDATE ")[1].split(")")[0].strip().strip('"') if "INTERNALDATE " in response_str else ""
            internaldate_match = internaldate_match.split(" UID")[0] if " UID" in internaldate_match else internaldate_match

            if isinstance(item[1], bytes):
                raw_body = item[1].decode("utf-8", errors="replace")
            else:
                raw_body = str(item[1]) if item[1] else ""

            msg = _parse_imap_raw_message(raw_body, uid, flags, internaldate_match)
            if msg:
                messages.append(msg)

    return messages


def _parse_imap_fetch_full(msg_data: list) -> list[EmailMessage]:
    """Parse IMAP FETCH response with full BODY[].

    Used by sync_messages() and get_message() for complete message data.
    """
    messages: list[EmailMessage] = []

    for item in msg_data:
        if item is None or not isinstance(item, tuple):
            continue

        response_str = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
        uid_match = re.search(r"UID\s+(\d+)", response_str)
        uid = uid_match.group(1) if uid_match else ""

        flags_match = re.search(r"FLAGS\s+\(([^)]*)\)", response_str)
        flags = flags_match.group(1).split() if flags_match else []

        internaldate_match = ""
        if "INTERNALDATE " in response_str:
            internaldate_match = response_str.split("INTERNALDATE ")[1].split(")")[0].strip().strip('"')
            internaldate_match = internaldate_match.split(" UID")[0] if " UID" in internaldate_match else internaldate_match

        raw_body = item[1].decode("utf-8", errors="replace") if isinstance(item[1], bytes) else str(item[1])

        msg = _parse_imap_raw_message(raw_body, uid, flags, internaldate_match)
        if msg:
            messages.append(msg)

    return messages


def _parse_imap_raw_message(
    raw: str, uid: str, flags: list[str], internaldate: str
) -> EmailMessage | None:
    """Parse raw RFC 2822 message text into our normalized EmailMessage."""
    try:
        parsed = email_lib.message_from_string(raw, policy=email_lib.policy.default)
    except Exception:
        return None

    # Headers
    subject = _decode_imap_header(str(parsed.get("Subject", "(no subject)")))
    from_addr = _parse_imap_address(str(parsed.get("From", "")))
    to_addrs = _parse_imap_address_list(str(parsed.get("To", "")))
    cc_addrs = _parse_imap_address_list(str(parsed.get("Cc", "")))
    bcc_addrs = _parse_imap_address_list(str(parsed.get("Bcc", "")))
    thread_id_raw = str(parsed.get("Message-ID", "")).strip()
    in_reply_to = str(parsed.get("In-Reply-To", "")).strip()

    # Body
    body_text = ""
    body_html = None
    attachments: list[Attachment] = []

    if parsed.is_multipart():
        for part in parsed.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                filename = part.get_filename() or f"attachment-{len(attachments)}"
                payload = part.get_payload(decode=True)
                size = len(payload) if payload else 0
                amime = part.get_content_type()
                att_id = f"{len(attachments) + 1}"
                attachments.append(Attachment(
                    id=f"{uid}_{att_id}",
                    filename=filename,
                    mime_type=amime,
                    size_bytes=size,
                    provider_attachment_id=att_id,
                ))
            elif content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_text = payload.decode(charset, errors="replace")
            elif content_type == "text/html" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_html = payload.decode(charset, errors="replace")
    else:
        content_type = parsed.get_content_type()
        payload = parsed.get_payload(decode=True)
        if payload:
            charset = parsed.get_content_charset() or "utf-8"
            if content_type == "text/html":
                body_html = payload.decode(charset, errors="replace")
            else:
                body_text = payload.decode(charset, errors="replace")

    # Flags
    is_seen = "\\Seen" in flags
    is_flagged = "\\Flagged" in flags
    is_draft = "\\Draft" in flags

    # Determine folder from flags
    if is_draft:
        folder = "DRAFTS"
    elif "\\Deleted" in flags:
        folder = "TRASH"
    else:
        folder = "INBOX"

    labels: list[str] = flags

    # Snippet: first 200 chars of body_text
    snippet = body_text[:200] if body_text else (body_html[:200] if body_html else "")

    # Use thread_id from In-Reply-To if Message-ID not present
    thread_id = thread_id_raw if thread_id_raw else (in_reply_to if in_reply_to else None)

    unsubscribe_link = _parse_list_unsubscribe(
        str(parsed.get("List-Unsubscribe", ""))
    ) or find_unsubscribe_link_in_html(body_html)

    return EmailMessage(
        provider_message_id=uid,
        thread_id=thread_id,
        folder=folder,
        labels=labels,
        from_address=from_addr,
        to_addresses=to_addrs,
        cc_addresses=cc_addrs,
        bcc_addresses=bcc_addrs,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        snippet=snippet,
        has_attachments=len(attachments) > 0,
        attachments=attachments,
        is_read=is_seen,
        is_starred=is_flagged,
        is_flagged=is_flagged,
        unsubscribe_link=unsubscribe_link,
        received_at=_parse_imap_internaldate(internaldate),
    )


def _parse_list_unsubscribe(header: str) -> str | None:
    """Pick the best link from a List-Unsubscribe header (https preferred)."""
    if not header:
        return None
    targets: list[str] = []
    for part in header.split(","):
        p = part.strip()
        if p.startswith("<") and p.endswith(">"):
            p = p[1:-1].strip()
        if p:
            targets.append(p)
    for t in targets:
        if t.lower().startswith("http"):
            return t
    for t in targets:
        if t.lower().startswith("mailto:"):
            return t
    return targets[0] if targets else None
