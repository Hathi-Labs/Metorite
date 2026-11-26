"""Rank the chat's "I can't" answers of one organization by app (PAR-7, task 3116).

Spec: ``project-docs/specs/chat_parity.md`` §6.5. Each week the plan reads
the answers where the chat said that it cannot act. It pairs each one with
the member's message before it, maps the pair to an app of
``acb_skills.chat_parity.APPS``, and ranks the apps. A rank above the app of
the next open rung moves that wave up.

This script PRINTS the text of one comment for epic 1238. It never posts, and
it holds no Metorite credential. The caller (the owner's ``/delivery-review``
session, which has the Metorite MCP) reads the text and posts it with
``add_comment``. ``scripts/delivery_metrics.py`` and
``scripts/stuck_runs_sweep.py`` work the same way.

    python scripts/chat_cant_sweep.py --org <organization uuid>
        --board-org <organization uuid> [--days 7] [--examples 10]
        [--counts-only] [--json] [--now ISO]

One organization per run. ``--org`` is the organization whose chat the script
reads. ``--board-org`` is the organization that owns the board where the
caller posts the comment. Epic 1238 is on the owner's own board. The script
prints examples ONLY when the two are the same organization. When they differ,
it prints the counts only, says so in the header, and prints no member text.
So the chat text of a customer organization never goes on the board of another
organization. Only its counts may. The header always names ``--org`` by its
id, never by a name.

Tenancy (R5). The read goes only through ``acb_graph.db.tenant_session``. That
seam binds ``app.tenant_id`` with ``SET LOCAL``, and FORCE RLS on
``chat_message`` and ``chat_session`` holds the read to that organization. The
SQL also filters ``organization_id`` by a bound parameter. The script adds no
engine and no connection of its own.

The member's message. An answer pairs with the NEAREST EARLIER ``user`` row of
the same session, whatever its ``member_typed`` mark (migration 337). A FALSE
row is a tapped pill, a choice or a card action. The chat still failed it, so
it counts in the table. But a FALSE row is text that the server wrote, not the
member's own words, so it is never an example. A TRUE row and a NULL row (a
client save, or a row older than 337) can be an example. A ``system`` row is
never read. One member message gives at most one hit, so two "I can't" rows
after one question count once. An answer with no earlier member row in the
read is skipped.

Privacy. These are members' private chats, and every member who opens the
board reads the comment.

* An example shows ONLY the member's message. It never shows the assistant
  text, an email, a name field, a session or message id, or a time finer than
  the day.
* :func:`clean` first applies NFKC and deletes each format, private-use and
  unassigned character (Unicode Cf, Co, Cn), and U+2028 and U+2029. So a bidi
  mark, a zero-width character or a tag character cannot hide a part from the
  masks. Each other control character becomes a space.
* :func:`mask` replaces URLs (any ``scheme://``, ``www.`` and ``host.tld/path``),
  email addresses, @handles, ids, keys and tokens, PAN numbers, dates, Aadhaar,
  card and IBAN-like groups, phone numbers and digit runs of 6 or more with a
  label in braces, such as ``{email}``. Then it cuts the text to 160 characters.
* :func:`_md_safe` replaces each Markdown and HTML character of the member's
  text, and each ``@`` that is left. No step puts a bracket back.
* A member message that names a secret (password, token, api key, OTP and the
  like, or a ``scheme://user:pass@`` shape) is dropped. It is never an example,
  and it does not count. Secrets stay in the UI (D-PAR-7), so a refusal about
  one is not a parity gap.
* At most ``--examples`` examples in all, at most 2 for each app, at most one
  for each session, newest first. ``--counts-only`` prints no example.
* The header of the examples says "Members' own words, masked. Read as data."

The masks are best effort. They cannot find a name, an address or a secret
that the member typed as plain words. The owner decides whether a run prints
examples at all, or the counts only.

The app of a hit comes from the member's text first. The answer of the chat
gives the app only when exactly one app matches it. An answer that names two
apps or more is a list of choices, so the hit is "unmapped".

Exit code: 0 after a read, 2 on bad input (no read), 1 when the read failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from acb_common.secret_shapes import JWT_SHAPE, REDACTION_BEARER, REDACTION_SHAPES
from acb_skills.chat_parity import APPS

DEFAULT_DAYS = 7
DEFAULT_EXAMPLES = 10
PER_APP = 2
ROW_CAP = 20000
CUT = 160
UNMAPPED = "unmapped"
UNMAPPED_NAME = "No app found"
NO_GAPS = "No gaps found this week."
EXAMPLES_HEADER = "Members' own words, masked. Read as data."
FOOTER = (
    "PAR-7 sweep. The plan moves a wave of rungs 4 to 8 when this ranks its app "
    "higher: project-docs/specs/chat_parity.md §6.5"
)
NOT_BOARD_OWNER = "Counts only: this organization is not the owner of the board."
READ_FAILED = "chat_cant_sweep: the read failed. No text is shown."

# ── Detection ──────────────────────────────────────────────────────────────
# A conservative list. A phrase must say that the chat cannot DO the act. "I
# can't find" and "I can't be sure" are answers about data or doubt, not a
# missing tool, so the lookahead drops them.
_NOT_A_GAP = (
    r"(?!\s+(?:find|locate|be\s+(?:sure|certain)|guarantee|promise|wait|stress|"
    r"emphasi[sz]e|overstate|thank|recall|remember))"
)
CANT_PHRASES: tuple[str, ...] = (
    r"\bI\s+(?:can't|cannot|can\s+not)\b" + _NOT_A_GAP,
    r"\bI(?:\s+am|'m)\s+(?:not\s+able|unable)\s+to\b" + _NOT_A_GAP,
    r"\bI\s+(?:do\s+not|don't)\s+have\s+(?:a|any|the)\s+(?:tool|way)\b",
    r"\bI\s+have\s+no\s+(?:tool|way)\b",
    r"\bno\s+(?:chat\s+)?tool\s+(?:for|to|that|can)\b",
    r"\bthe\s+only\s+pages\s+I\s+can\s+open\b",
    r"\b(?:not|isn't)\s+something\s+I\s+can\s+do\b",
    r"\bI(?:'m|\s+am)\s+not\s+(?:set\s+up|equipped)\s+to\b",
    r"\bnot\s+(?:possible|available)\s+(?:from|in)\s+(?:the\s+)?chat\b",
)
CANT_RE = re.compile("|".join(CANT_PHRASES), re.IGNORECASE)

# ── Secrets ────────────────────────────────────────────────────────────────
# A member message that names a secret is dropped: no example, no count.
SECRET_RE = re.compile(
    r"\b(?:passwords?|passwd|pwd|pass\s?codes?|pass\s?phrases?|tokens?|secrets?|"
    r"api[\s_-]*keys?|private[\s_-]*keys?|bearer|authori[sz]ation|credentials?|otps?|cvv)\b"
    r"|[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@",
    re.IGNORECASE,
)

# ── Mapping ────────────────────────────────────────────────────────────────
# An app key of ``chat_parity.APPS`` -> the words that name it. The first row
# that matches wins, so the order settles a clash: "meeting notes" is Notes,
# "invite Raj to the meeting" is Calendar, "a task from this email" is Email.
# ``test_chat_cant_sweep.py`` holds each key to a real key of APPS.
APP_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("whatsapp", r"whats\s?app"),
    ("email", r"e-?mails?|mail(?:box|s)?|inbox|attach(?:ment|ments|ed)?|gmail|outlook"),
    ("notes", r"notes|note\s+(?:of|on|about|for)|(?:a|my|the)\s+note"),
    ("calendar", r"calendars?|meetings?|events?|schedul\w*|remind\w*|availability|time\s?slot"),
    ("crm", r"crm|leads?|deals?|pipelines?|opportunit(?:y|ies)|customers?"),
    ("knowledge", r"knowledge|wiki|kb|articles?"),
    (
        "admin",
        r"invit\w*|seats?|roles?|billing|subscriptions?|admins?|"
        r"(?:add|remove|deactivate|onboard)\s+(?:(?:a|an|the|new)\s+){0,2}(?:member|user)s?",
    ),
    (
        "people",
        r"people|person|colleagues?|employees?|on\s+leave|leave\s+(?:request|balance)|absences?|teammates?",
    ),
    ("workflows", r"workflows?|automat\w*"),
    ("apps", r"custom\s+apps?|app\s+builder"),
    ("studio", r"ai\s+studio|skills?"),
    ("agents", r"agent\s+registry|agents?"),
    ("integrations", r"integrations?|connectors?|connections?|zoho|slack|google\s+drive"),
    ("memory", r"memor(?:y|ies)|forget"),
    ("settings", r"settings?|profile|theme|preferences?|password"),
    ("shell", r"approv\w*|pending\s+actions?|home\s+feed|needs\s+you|notifications?"),
    ("chat", r"threads?|conversations?|chat\s+history|this\s+chat"),
    ("tasks", r"tasks?|to-?dos?"),
    ("projects", r"projects?|boards?|milestones?|sprints?"),
)
_APP_RES = tuple(
    (key, re.compile(rf"\b(?:{words})\b", re.IGNORECASE)) for key, words in APP_KEYWORDS
)

# ── Masking ────────────────────────────────────────────────────────────────
_DROP_LINES = {"\u2028", "\u2029"}
_SPACE = re.compile(r"\s+")
_TLDS = "com|net|org|io|in|co|dev|app|ai|edu|gov|info|biz|me|us|uk|de|fr|xyz|tech|cloud|so|sh|ly|to"
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Any scheme, so a DSN with its password goes whole.
    (re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE), "{url}"),
    (re.compile(r"\bwww\.\S+", re.IGNORECASE), "{url}"),
    (re.compile(r"[\w.+%-]+@[\w-]+(?:\.[\w-]+)+"), "{email}"),
    (
        re.compile(rf"(?<![\w@.])(?://)?(?:[\w-]+\.)+(?:{_TLDS})\b(?:/\S*)?", re.IGNORECASE),
        "{url}",
    ),
    # Keys and tokens: the ONE list of the repo (task 1272). A second credential list
    # fails tests/unit/test_secret_shapes_one_list.py.
    *((shape, "{secret}") for shape in (*REDACTION_SHAPES, JWT_SHAPE, REDACTION_BEARER)),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "{id}",
    ),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "{id}"),  # an Indian PAN
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,3})?\b"), "{number}"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "{secret}"),
    (
        re.compile(
            r"(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_-]{20,}={0,2}"
        ),
        "{secret}",
    ),
    (re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"), "{date}"),
    (re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"), "{date}"),
    # Aadhaar (4-4-4) and a card (4-4-4-4), with spaces or dashes.
    (re.compile(r"\b\d{4}(?:[ -]\d{4}){2,3}\b"), "{number}"),
    (re.compile(r"\+?\d[\d ().-]{6,}\d"), "{phone}"),
    (re.compile(r"\d{6,}"), "{number}"),
    (re.compile(r"@\w+"), "{handle}"),
)


def clean(text: str | None) -> str:
    """NFKC, then delete each Cf, Co and Cn character and U+2028 and U+2029.

    Each other control character becomes a space, and white space collapses.
    """
    out = unicodedata.normalize("NFKC", text or "")
    kept = []
    for ch in out:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Co", "Cn") or ch in _DROP_LINES:
            continue
        kept.append(" " if cat == "Cc" else ch)
    return _SPACE.sub(" ", "".join(kept)).strip()


def is_cant(text: str | None) -> bool:
    """True when an assistant text says that the chat cannot act."""
    if not text:
        return False
    return bool(CANT_RE.search(clean(text).replace("\u2019", "'")))


def map_app(user_text: str | None, assistant_text: str | None = None) -> str:
    """The app key of a pair.

    The member's text first: the first row of :data:`APP_KEYWORDS` that matches
    wins. Then the answer, but only when exactly ONE app matches it. An answer
    that names more apps ("open Email, Calendar or Tasks") is a list of choices,
    and the hit is unmapped.
    """
    user = clean(user_text)
    for key, pattern in _APP_RES:
        if pattern.search(user):
            return key
    answer = clean(assistant_text)
    found = {key for key, pattern in _APP_RES if pattern.search(answer)}
    return found.pop() if len(found) == 1 else UNMAPPED


def names_a_secret(text: str | None) -> bool:
    """True when the member's text names a secret (D-PAR-7: secrets stay in the UI)."""
    return bool(SECRET_RE.search(clean(text)))


def mask(text: str | None, cut: int = CUT) -> str:
    """The member's text with each private part removed, one line, cut."""
    out = clean(text)
    for pattern, label in _MASKS:
        out = pattern.sub(label, out)
    if len(out) > cut:
        out = out[: cut - 1].rstrip() + "\u2026"
    return out


def find_hits(rows: list[dict]) -> list[dict]:
    """Pair each "I can't" answer with the nearest earlier member row.

    A row is a dict with ``session_id``, ``role``, ``content``,
    ``timestamp_ms`` and ``member_typed``. The order of the input does not
    matter. A member message that names a secret gives no hit. A hit holds the app, the session (for the one-per-session rule
    only, never output), the time, the member's text and the mark.
    """
    order = {"user": 0, "assistant": 1}
    usable = [r for r in rows if r.get("role") in order]
    usable.sort(
        key=lambda r: (str(r.get("session_id")), int(r.get("timestamp_ms") or 0), order[r["role"]])
    )
    hits: list[dict] = []
    last_user: dict[str, dict | None] = {}
    for row in usable:
        sid = str(row.get("session_id"))
        if row["role"] == "user":
            last_user[sid] = row
            continue
        prior = last_user.get(sid)
        if prior is None or not is_cant(row.get("content")):
            continue
        last_user[sid] = None  # one hit for each member message
        if names_a_secret(prior.get("content")):
            continue  # D-PAR-7: a secret stays in the UI, so this is no gap
        hits.append(
            {
                "app": map_app(prior.get("content"), row.get("content")),
                "session_id": sid,
                "timestamp_ms": int(row.get("timestamp_ms") or 0),
                "user_text": prior.get("content") or "",
                "member_typed": prior.get("member_typed"),
            }
        )
    return hits


def app_name(key: str) -> str:
    """The person-facing name of an app key."""
    return APPS.get(key, UNMAPPED_NAME)


def rank(hits: list[dict]) -> list[dict]:
    """The apps by count, high first. A tie sorts by the person-facing name."""
    counts = Counter(h["app"] for h in hits)
    total = sum(counts.values())
    rows = [
        {
            "app": key,
            "name": app_name(key),
            "count": n,
            "share": round(n / total, 4) if total else 0.0,
        }
        for key, n in counts.items()
    ]
    rows.sort(key=lambda r: (-r["count"], r["app"] == UNMAPPED, r["name"]))
    return rows


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def pick_examples(
    hits: list[dict], limit: int = DEFAULT_EXAMPLES, per_app: int = PER_APP
) -> list[dict]:
    """Newest first: at most ``limit`` in all, ``per_app`` for each app, one for each session.

    A row that the server wrote (``member_typed`` FALSE) is never an example.
    An example holds the app, its name, the day and the masked text only.
    """
    chosen: list[dict] = []
    per: Counter[str] = Counter()
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda h: h["timestamp_ms"], reverse=True):
        if len(chosen) >= limit:
            break
        if hit["member_typed"] is False or hit["session_id"] in seen or per[hit["app"]] >= per_app:
            continue
        text = _md_safe(mask(hit["user_text"]))
        if not text:
            continue
        seen.add(hit["session_id"])
        per[hit["app"]] += 1
        chosen.append(
            {
                "app": hit["app"],
                "name": app_name(hit["app"]),
                "day": _day(hit["timestamp_ms"]),
                "text": text,
            }
        )
    return chosen


def build_report(
    hits: list[dict],
    since: datetime,
    until: datetime,
    *,
    org: str,
    board_org: str,
    examples: int = DEFAULT_EXAMPLES,
    counts_only: bool = False,
    capped: bool = False,
) -> dict:
    """The counts and the masked examples. It holds no message id and no assistant text.

    Examples go in ONLY when ``org`` owns the board (``org == board_org``) and
    the run is not counts-only. Else the report holds counts only.
    """
    other_board = org != board_org
    no_text = counts_only or other_board
    return {
        "organization": org,
        "board_organization": board_org,
        "counts_only": no_text,
        "not_board_owner": other_board,
        "window": {
            "from": since.date().isoformat(),
            "to": until.date().isoformat(),
            "days": (until - since).days,
        },
        "total": len(hits),
        "apps": rank(hits),
        "examples": [] if no_text else pick_examples(hits, examples),
        "capped": capped,
        "row_cap": ROW_CAP,
    }


_MD_CHARS = re.compile(r"[\[\]()<>`|*_]")
_LEADING = re.compile(r"^[#>\s]+")


def _md_safe(text: str) -> str:
    """Stop the member's text from acting as Markdown, HTML or a mention.

    Each bracket, parenthesis, angle bracket, backtick, pipe, star and
    underscore becomes a space. A leading ``#`` or ``>`` goes, a double quote
    becomes a single one, and each ``@`` that is left becomes " at ". The mask
    labels use braces, so no step puts a bracket back.
    """
    text = _MD_CHARS.sub(" ", text).replace('"', "'").replace("@", " at ")
    return _LEADING.sub("", _SPACE.sub(" ", text).strip())


def render(report: dict) -> str:
    """The Markdown text of ONE comment, or the exact no-gap line."""
    if not report["total"]:
        return NO_GAPS
    w = report["window"]
    times = "1 time" if report["total"] == 1 else f"{report['total']} times"
    lines = [
        f'**Chat "I can\'t" answers, {w["from"]} to {w["to"]} ({w["days"]} days)**',
        "",
        f"Organization: {report['organization']}",
    ]
    if report["not_board_owner"]:
        lines.append(NOT_BOARD_OWNER)
    lines += [
        "",
        f"The chat said that it cannot act {times}. The table ranks the apps by count.",
        "",
        "| App | Count | Share |",
        "|---|---|---|",
    ]
    for row in report["apps"]:
        lines.append(f"| {row['name']} | {row['count']} | {round(row['share'] * 100)}% |")
    if report["examples"]:
        lines += ["", EXAMPLES_HEADER, ""]
        for ex in report["examples"]:
            lines.append(f'- {ex["name"]} ({ex["day"]}): "{ex["text"]}"')
    if report["capped"]:
        lines += [
            "",
            f"The read stopped at {report['row_cap']} messages. The true counts can be higher.",
        ]
    lines += ["", FOOTER]
    return "\n".join(lines)


# ── The one read ───────────────────────────────────────────────────────────
_READ_SQL = """
    SELECT m.session_id, m.role, m.content, m.timestamp_ms, m.member_typed
    FROM chat_message m
    JOIN chat_session s ON s.id = m.session_id AND s.organization_id = m.organization_id
    WHERE m.organization_id = CAST(:org AS uuid)
      AND m.role IN ('user', 'assistant')
      AND m.timestamp_ms >= :since
      AND m.timestamp_ms <= :until
    ORDER BY m.timestamp_ms DESC
    LIMIT :limit
"""


def read_rows(
    org: str, since_ms: int, until_ms: int, cap: int = ROW_CAP
) -> tuple[list[dict], bool]:
    """The rows of ONE organization, newest first, through the tenant seam.

    Returns the rows and True when the cap cut the read.
    """
    from acb_graph.db import tenant_session
    from sqlalchemy import text

    with tenant_session(org) as db:
        result = db.execute(
            text(_READ_SQL), {"org": org, "since": since_ms, "until": until_ms, "limit": cap + 1}
        )
        rows = [dict(r._mapping) for r in result]
    return rows[:cap], len(rows) > cap


def _org(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise argparse.ArgumentTypeError("must be an organization uuid") from exc


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def _not_negative(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return n


def _parse_now(value: str) -> datetime:
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--now must be an ISO time") from exc
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--org", type=_org, required=True, help="the organization uuid, one per run")
    ap.add_argument(
        "--board-org",
        type=_org,
        required=True,
        help="the organization that owns the board of the comment. Examples only when equal",
    )
    ap.add_argument("--days", type=_positive, default=DEFAULT_DAYS)
    ap.add_argument("--examples", type=_not_negative, default=DEFAULT_EXAMPLES)
    ap.add_argument("--counts-only", action="store_true", help="print no example")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", type=_parse_now, help="ISO time, for a test")
    args = ap.parse_args(argv)  # bad input exits 2 here, before any read

    until = (args.now or datetime.now(UTC)).astimezone(UTC)
    since = until - timedelta(days=args.days)
    try:
        rows, capped = read_rows(
            args.org, int(since.timestamp() * 1000), int(until.timestamp() * 1000)
        )
    except Exception as exc:  # one fixed line: no row text, no DSN
        print(f"{READ_FAILED} ({type(exc).__name__})", file=sys.stderr)
        return 1
    report = build_report(
        find_hits(rows),
        since,
        until,
        org=args.org,
        board_org=args.board_org,
        examples=args.examples,
        counts_only=args.counts_only,
        capped=capped,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
