"""The member's assistant profile: its shape, its lenient read and its block.

WS-63. Spec: ``project-docs/specs/personalization.md`` §4.2 and §5.1. The
column is ``user_settings.assistant_profile`` (migration 294).

This module is the ONE parser of the stored profile. Two callers use it:

* ``gateway.routes.admin.assistant_profile`` (PZ-1), the API of §4.3. It
  keeps the pydantic models, the write SQL and the routes, and imports the
  names below.
* ``orchestrator.executor._member_profile_block`` (PZ-2), the per-turn block
  of §5.1. It runs :data:`READ_SQL` and :func:`render_block`.

The precedent is ``acb_common.number_system``. This module opens no
connection and imports no web framework. :data:`READ_SQL` runs in the
caller's session, which binds the tenant (R5).

**The flag.** ``ASSISTANT_PROFILE_ENABLED`` (``assistant_profile_enabled``),
default OFF. :func:`enabled` is its one reader. OFF gives no block, so the
per-turn context stays as it was before PZ-2.

Fences: ``tests/unit/test_assistant_profile.py`` and
``tests/unit/test_executor_profile_block.py``.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

#: The version of the shape. The first write sets it.
PROFILE_VERSION = 1
#: The most characters of the whole profile, as compact JSON (§4.2 budget).
PROFILE_MAX = 6_000
#: ``address.name``.
NAME_MAX = 40
#: ``style.language``, a BCP 47 tag.
LANGUAGE_MAX = 16
#: ``style.notes``: the most items, and the longest item.
NOTES_MAX = 5
NOTE_MAX = 200
#: ``facts``: the most items, and the longest text.
FACTS_MAX = 40
FACT_MAX = 300
#: ``aliases``: the most items, and the longest alias.
ALIASES_MAX = 50
ALIAS_MAX = 60
#: The longest address that an alias takes (RFC 5321).
EMAIL_MAX = 254

PUT_KEYS: tuple[str, ...] = ("learning", "address", "style")
FORMALITY: tuple[str, ...] = ("casual", "neutral", "formal")
GREETING: tuple[str, ...] = ("none", "brief", "warm")
LENGTH: tuple[str, ...] = ("short", "default", "detailed")
FACT_KINDS: tuple[str, ...] = ("about", "routine", "preference", "work", "relationship")
FACT_SOURCES: tuple[str, ...] = ("chat", "whatsapp", "settings")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")


def empty_profile() -> dict[str, Any]:
    """The profile of a member who set nothing (§4.2 defaults)."""
    return {"v": PROFILE_VERSION, "learning": True, "address": {}, "style": {},
            "aliases": [], "facts": []}


# ── Strict cleaning (the write) ────────────────────────────────────────────


def _text(value: Any, cap: int, what: str) -> str:
    """One line of free text: a string, trimmed, not empty, at most ``cap``
    characters, with no control character and no lone surrogate."""
    if not isinstance(value, str):
        raise ValueError(f"{what} is text")
    out = value.strip()
    if not out:
        raise ValueError(f"{what} is empty")
    if len(out) > cap:
        raise ValueError(f"{what} is longer than {cap} characters")
    # The text goes into a prompt. Postgres stores no NUL, and jsonb refuses
    # a lone surrogate (a JS `slice()` through an emoji), which is a 503 and
    # not a 422 unless the route refuses it first.
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in out):
        raise ValueError(f"{what} holds a control character or a lone surrogate")
    return out


def _choice(value: Any, choices: tuple[str, ...], what: str) -> str:
    if value not in choices:
        raise ValueError(f"{what} is one of {', '.join(choices)}")
    return str(value)


def _email(value: Any) -> str:
    out = _text(value, EMAIL_MAX, "the email")
    if not _EMAIL.match(out):
        raise ValueError("the email is not an address")
    return out


def _object(value: Any, keys: tuple[str, ...], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is an object")
    unknown = [k for k in value if k not in keys]
    if unknown:
        # The message names the known keys, never the member's. A key with a
        # lone surrogate cannot be encoded, and it made this a 500 (PZ-1
        # review, P2).
        raise ValueError(f"{what} has an unknown key. Known keys: {', '.join(keys)}")
    return value


def _clean_address(value: Any) -> dict[str, Any]:
    raw = _object(value, ("name", "formality", "greeting"), "the address")
    out: dict[str, Any] = {}
    if "name" in raw:
        out["name"] = _text(raw["name"], NAME_MAX, "address.name")
    if "formality" in raw:
        out["formality"] = _choice(raw["formality"], FORMALITY, "address.formality")
    if "greeting" in raw:
        out["greeting"] = _choice(raw["greeting"], GREETING, "address.greeting")
    return out


def _clean_style(value: Any) -> dict[str, Any]:
    raw = _object(value, ("length", "language", "notes"), "the style")
    out: dict[str, Any] = {}
    if "length" in raw:
        out["length"] = _choice(raw["length"], LENGTH, "style.length")
    if "language" in raw:
        lang = _text(raw["language"], LANGUAGE_MAX, "style.language")
        if not _LANGUAGE.match(lang):
            raise ValueError("style.language is a BCP 47 tag")
        out["language"] = lang
    if "notes" in raw:
        notes = raw["notes"]
        if not isinstance(notes, list):
            raise ValueError("style.notes is a list of lines")
        if len(notes) > NOTES_MAX:
            raise ValueError(f"style.notes holds at most {NOTES_MAX} lines")
        out["notes"] = [_text(n, NOTE_MAX, "a style note") for n in notes]
    return out


# ── Lenient reading (the answer) ───────────────────────────────────────────


def _lenient(clean: Any, value: Any) -> Any:
    try:
        return clean(value)
    except ValueError:
        return None


def _stored_fact(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    text = _lenient(lambda v: _text(v, FACT_MAX, "a fact"), item.get("text"))
    if text is None:
        return None
    # A kind this build does not know (a later slice may add one) reads as
    # ``about``. The fact still counts against the caps, so the member must
    # see it to delete it (PZ-1 verify, finding 2).
    kind = item.get("kind") if item.get("kind") in FACT_KINDS else "about"
    out: dict[str, Any] = {"id": item["id"], "text": text, "kind": kind}
    if item.get("source") in FACT_SOURCES:
        out["source"] = item["source"]
    if isinstance(item.get("at"), str):
        out["at"] = item["at"]
    return out


def _stored_alias(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    alias = _lenient(lambda v: _text(v, ALIAS_MAX, "an alias"), item.get("alias"))
    email = _lenient(_email, item.get("email"))
    if alias is None or email is None:
        return None
    return {"id": item["id"], "alias": alias, "email": email}


def _stored_keys(raw: Any, clean: Any) -> dict[str, Any]:
    """The keys of ``address`` or ``style`` that pass their own check."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        one = _lenient(clean, {k: v})
        if one:
            out.update(one)
    return out


def _as_dict(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def stored_profile(raw: Any) -> dict[str, Any]:
    """A stored value, as the routes answer it. Lenient on purpose: a key or
    an item of the wrong shape drops from the answer rather than fail the
    read. A key that is absent takes its §4.2 default."""
    raw = _as_dict(raw)
    out = empty_profile()
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("learning"), bool):
        out["learning"] = raw["learning"]
    out["address"] = _stored_keys(raw.get("address"), _clean_address)
    out["style"] = _stored_keys(raw.get("style"), _clean_style)
    for key, one, cap in (("facts", _stored_fact, FACTS_MAX),
                          ("aliases", _stored_alias, ALIASES_MAX)):
        items = raw.get(key)
        if isinstance(items, list):
            out[key] = [x for x in map(one, items) if x is not None][:cap]
    return out


def profile_size(profile: Any) -> int:
    """The characters of a profile, as compact JSON. The §4.2 budget."""
    return len(json.dumps(profile, ensure_ascii=False, separators=(",", ":")))


# ── The flag ───────────────────────────────────────────────────────────────


def enabled() -> bool:
    """True when ``ASSISTANT_PROFILE_ENABLED`` is on. Fails closed."""
    try:
        from acb_common import get_settings

        return getattr(get_settings(), "assistant_profile_enabled", False) is True
    except Exception:
        return False


# ── The read of the run (PZ-2) ─────────────────────────────────────────────

#: The member's stored profile in the bound org. ``:org`` is the tenant of
#: the run and ``:m`` the run's verified member, both from the run boundary
#: and never from the payload (R5). The SQL names the org beside RLS, not
#: instead of it. ``user_settings.user_id`` is the address as the session
#: gave it, so the member matches in any case, as ``number_system.READ_SQL``
#: does. When two rows match, the row with a profile wins, then the
#: lower-case row.
READ_SQL = (
    "SELECT assistant_profile FROM user_settings "
    "WHERE organization_id = CAST(:org AS uuid) AND lower(user_id) = lower(:m) "
    "ORDER BY (assistant_profile IS NULL), (user_id <> lower(user_id)) "
    "LIMIT 1"
)


# ── The block (§5.1) ───────────────────────────────────────────────────────

#: The most characters of the rendered block (§4.2, about 1,500 tokens).
RENDER_MAX = 6_000
#: The first line of the block.
BLOCK_HEADER = "## About the member (their own profile — they can see and change it)"
#: The line before the facts.
FACTS_HEADER = "What they asked you to remember:"
#: The last line of the block.
BLOCK_FOOTER = "If they ask what you know about them, answer from this block only."


def _one_line(value: Any) -> str:
    """*value* as one line: each run of white space becomes one space."""
    return " ".join(str(value).split())


def _sentence(value: str) -> str:
    """*value* with a full stop, unless it already ends with one."""
    return value if value[-1:] in ".!?" else f"{value}."


def _address_line(address: dict[str, Any]) -> str:
    bits: list[str] = []
    if address.get("name"):
        bits.append(f'Address them as "{_one_line(address["name"])}".')
    if address.get("formality"):
        bits.append(f"Tone: {address['formality']}.")
    greeting = address.get("greeting")
    if greeting == "none":
        bits.append("Greeting: none.")
    elif greeting:
        bits.append(f"Greeting: {greeting}, only in the first reply of a chat.")
    return " ".join(bits)


def _style_line(style: dict[str, Any], notes: list[str]) -> str:
    bits: list[str] = []
    if style.get("length"):
        bits.append(f"Answer length: {style['length']}.")
    if style.get("language"):
        bits.append(f"Language: {_one_line(style['language'])}.")
    if notes:
        bits.append("Notes: " + " ".join(_sentence(n) for n in notes))
    return " ".join(bits)


def _aliases_line(aliases: list[dict[str, Any]]) -> str:
    if not aliases:
        return ""
    pairs = ", ".join(f'"{a["alias"]}" = {a["email"]}' for a in aliases)
    return f"People they name: {pairs}."


def _fact_line(fact: dict[str, Any]) -> str:
    day = fact["at"][:10] if fact["at"] else ""
    tail = f"{fact['kind']}, {day}" if day else fact["kind"]
    return f"- [{fact['id']}] {fact['text']} ({tail})"


def _compose(address: str, style: str, aliases: list[dict[str, Any]],
             facts: list[dict[str, Any]]) -> str:
    lines = [BLOCK_HEADER]
    lines += [x for x in (address, style, _aliases_line(aliases)) if x]
    if facts:
        lines.append(FACTS_HEADER)
        lines += [_fact_line(f) for f in facts]
    lines.append(BLOCK_FOOTER)
    return "\n".join(lines)


def render_block(raw: Any) -> str:
    """The per-turn block of §5.1 for the stored value *raw*, or ``""``.

    Pure, apart from one log line when the cap sheds an item. It reads the
    value through :func:`stored_profile`, so an item of the wrong shape
    drops. An empty profile (no address, no style, no alias, no fact) gives
    ``""``. ``learning`` off still renders: it stops new learning (PZ-6),
    and the member's saved words still apply.

    The block is at most :data:`RENDER_MAX` characters. Over the cap it
    sheds whole items in one fixed order: the facts, oldest first (by
    ``at``, then by position), then the aliases from the end, then the
    style notes from the end. It never cuts a line, and it keeps the
    header, the address line and the style line. The same value always
    gives the same bytes, so the block sits well in a cached prefix.
    """
    profile = stored_profile(raw)
    address = {k: v for k, v in profile["address"].items()}
    style = {k: v for k, v in profile["style"].items()}
    notes = [_one_line(n) for n in style.pop("notes", []) if _one_line(n)]
    aliases = [{"alias": _one_line(a["alias"]), "email": _one_line(a["email"])}
               for a in profile["aliases"]]
    facts = [{"id": _one_line(f["id"]), "text": _one_line(f["text"]),
              "kind": f["kind"], "at": _one_line(f.get("at") or ""), "n": n}
             for n, f in enumerate(profile["facts"])]
    if not (address or style or notes or aliases or facts):
        return ""
    address_line = _address_line(address)
    dropped = 0
    # The oldest first: an undated fact sorts as the oldest of all.
    shed_order = [f["n"] for f in sorted(facts, key=lambda f: (f["at"], f["n"]))]
    while True:
        block = _compose(address_line, _style_line(style, notes), aliases, facts)
        if len(block) <= RENDER_MAX:
            break
        if shed_order:
            gone = shed_order.pop(0)
            facts = [f for f in facts if f["n"] != gone]
        elif aliases:
            aliases = aliases[:-1]
        elif notes:
            notes = notes[:-1]
        else:
            break
        dropped += 1
    if dropped:
        # The size and the count only. Never the member's text.
        logger.info("assistant_profile.block_shed",
                    extra={"size": len(block), "dropped": dropped})
    return block
