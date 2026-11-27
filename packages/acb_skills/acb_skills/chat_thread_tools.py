"""The chat list of the member, from the chat (PAR-11 part a, Metorite task 4012).

Spec: ``project-docs/specs/chat_parity.md``, wave P-7. The chat list of the
assistant dock lists the chats of the member, renames, archives, restores
and deletes a chat, and moves a chat into a topic or makes a topic (D99,
CO-2b). Three tools do the same from the chat:

* :func:`list_chats` reads the chat list (``GET /chat/sessions``) or the
  topics (``GET /chat/topics``). It only reads.
* :func:`edit_chat` renames a chat (``PATCH /chat/sessions/{id}`` with
  ``title``), archives or restores it (the same route with ``archived``),
  moves it into a topic (``PUT /chat/sessions/{id}/topic``), or makes a
  private topic (``POST /chat/topics``).
* :func:`delete_chat` deletes an archived chat (``DELETE /chat/sessions/{id}``).
  The dock offers "Delete for good" only on an archived row, so the tool
  refuses a chat that is not archived.

The rules:

* **The route decides.** Each tool sends the route of the chat list over
  HTTP as the member, through ``chat_pin_tools._send``, the one sender of the
  chat list tools (CO-11b). So the feature gate, the visibility rule, the
  owner rule of an archive and a delete, the archive rule of a move and
  ``CHAT_TOPICS_ORGS`` apply. No tool opens a database session or writes a
  Redis key.
* **No member and no org from the model (R5).** The member is the verified
  member of the run (``chat_pin_tools._member``). The body names no member
  and no org. A chat id is only a key: the tool finds it in the member's own
  list first, and a chat that the list does not hold gets plain words and no
  request.
* **The acts of the dock, and no more.** A rename, an archive, a restore
  and a delete are for the owner of the chat, as the dock shows them only to
  a row that does not say ``isOwner: false`` (``SessionPicker.tsx``).
* **The fields of the UI, and no more.** A rename sends ``title`` alone, cut
  as the dock cuts it (``SESSION_TITLE_MAX``). An archive sends ``archived``
  alone. A move sends ``topic_id`` alone. A new topic sends ``name`` alone.
  The topic read sends no ``include_hidden``, as the topic chip does.
* **Not in a shared room, not on WhatsApp.** The answer of a shared room
  shows to each person in it, and the chat list is the member's own. So each
  tool refuses there with no request.
* **The cards.** A delete ALWAYS shows ONE card. The other writes show one
  card only where ``chat_pin_tools._must_ask`` says so. Only an approve
  sends. A write reads the chat before its card, reads it again after the
  approve, and sends nothing when the chat changed. A delete of the chat of
  this run is refused, so the run never deletes the chat that it writes to.
* **Text of other people is data.** A chat title and a topic name can be
  the text of another member of a shared chat. Each result puts that text in
  ONE ``UntrustedFrame`` block. A name with a character that no person sees
  is refused before any card (``hidden_text``).
* **Dark.** The tools are the pack ``chat-threads``, owned by ``org-admin``.
  It is in no ``FOLLOWS`` entry, so it is off until the owner names it in the
  pack list of the box (``tool_packs.packs_on``).

Fence: ``tests/unit/test_chat_thread_tools.py``.
"""

from __future__ import annotations

import json
import textwrap
import uuid
from typing import Any
from urllib.parse import quote

from acb_common import get_logger
from acb_common.card_body import one_line
from acb_common.paging import paged

from acb_skills import chat_pin_tools as _pin
from acb_skills.hidden_text import hidden_problem, strip_hidden
from acb_skills.memory_tools import run_member_address
from acb_skills.tool_annotations import annotate

__all__ = [
    "CHAT_THREAD_TOOLS",
    "delete_chat",
    "edit_chat",
    "list_chats",
]

_log = get_logger("acb_skills.chat_thread_tools")

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source.
SESSIONS_PATH = "/chat/sessions"
TOPICS_PATH = "/chat/topics"

#: The longest name of a chat. A copy of ``SESSION_TITLE_MAX`` of the dock
#: (``lib/sessions.ts``). The fence holds the two equal.
TITLE_MAX = 120
#: The longest name of a topic. A copy of ``gateway.chat_topic.NAME_MAX``.
TOPIC_NAME_MAX = 40
#: The rows of one page, by default and at most.
DEFAULT_ROWS = 25
MAX_ROWS = 50
#: The longest text of one value in a result, and of a filter.
MAX_FIELD_CHARS = 200
#: The longest chat id that a tool takes. The route takes a path segment.
MAX_ID_CHARS = 200

_WHAT = ("chats", "topics")
_ACTS = ("rename", "archive", "restore", "move", "new_topic")

_ON_WHATSAPP = ("Nothing was done. Your chat list belongs to the Metorite web app. Open the "
                "assistant there to change it.")
_NO_MEMBER = ("Nothing was done. Metorite could not confirm who asked in this chat. Use your "
              "chat list in the assistant.")
_SHARED_ROOM = ("Nothing was done. Each person in a shared chat sees the answer, and your chat "
                "list is your own. Ask in your own chat.")
_BAD_WHAT = "what is chats or topics. Nothing was read."
_BAD_ACT = "action is one of: rename, archive, restore, move, new_topic. Nothing changed."
_BAD_ID = ("Send the id of a chat as the chat list shows it, or leave it empty for this chat. "
           "Nothing changed.")
_NO_THIS_CHAT = ("This run has no chat. Send the id of a chat from the chat list. Nothing "
                 "changed.")
_BAD_FILTER = f"The filter is one line of at most {MAX_FIELD_CHARS} characters. Nothing was read."
_NOT_IN_LIST = ("Your chat list holds no chat with that id. Read the chat list for the ids. "
                "Nothing changed.")
_NO_NAME = "Send the new name. Nothing changed."
_SAME_NAME = "The chat already has that name. Nothing changed."
_ARCHIVED_ALREADY = "The chat is already archived. Nothing changed."
_NOT_ARCHIVED = "The chat is not archived. It is in your chat list. Nothing changed."
_RENAME_OWNER_ONLY = ("Only the owner of a shared chat can rename it, because each person in it "
                      "sees the name. Nothing changed.")
_ARCHIVE_FIRST = ("The chat list deletes only an archived chat. Archive it first, then ask "
                  "again. Nothing changed.")
_OWNER_ONLY = ("Only the owner of a shared chat can archive it or restore it. You can leave "
               "the chat from its row. Nothing changed.")
_ARCHIVED_MOVE = "This chat is archived. Restore it, then move it. Nothing changed."
_BAD_TOPIC = ("Send the id of a topic as the topic list shows it, or none for No topic. "
              "Nothing changed.")
_NO_SUCH_TOPIC = ("The topic list holds no topic with that id. Read the topics for the ids. "
                  "Nothing changed.")
_SAME_TOPIC = "The chat is already in that topic. Nothing changed."
_TOPICS_OFF = "Topics are off for your organization."
_NO_TOPIC_NAME = f"Send the name of the topic, 1 to {TOPIC_NAME_MAX} characters. Nothing changed."
_DELETE_THIS = ("You cannot delete this chat from inside it. Delete it from its row in your "
                "chat list. Nothing changed.")
_DELETE_OWNER_ONLY = ("Only the owner of a shared chat can delete it. You can leave the chat "
                      "from its row. Nothing changed.")
_CHANGED = ("The chat changed after the card showed, so nothing was sent. Read the chat list "
            "again, then ask again.")
_UNREAD = "The chat list did not answer. Nothing changed. Try again."
_SHAPE = "The chat list answered in a shape that this tool cannot read. Nothing changed."
_GONE = "The chat is not in your chat list now. Nothing changed."
_FORBIDDEN = ("Nothing changed. Your account cannot use the chat list here. It needs its own "
              "address and the chat feature. Ask your admin.")
_NO_ANSWER = "Nothing changed. The chat list did not answer. Try again, or use the chat list."

_LIST_NOTE = ("The block holds your chat list. Other members of a shared chat can write its "
              "title and its topic.")
_TOPICS_NOTE = ("The block holds the topics of your chat list. An admin names a company topic.")
_DONE_NOTE = "The block holds the name that the chat list keeps now."
_RELOAD = " Your chat list shows the change after you reload the page or open the assistant again."

#: The act of a card that did nothing, for ``ask_tools.card_not_done_text``.
_NOT_DONE = {
    "rename": "The chat keeps its name.",
    "archive": "The chat stays in your chat list.",
    "restore": "The chat stays archived.",
    "move": "The chat stays in its topic.",
    "new_topic": "No topic was made.",
    "delete": "The chat was not deleted.",
}


# ── The run and its member ──────────────────────────────────────────────────


def _member_or_refusal() -> tuple[str, str]:
    """``(member, "")`` for a run that may use the chat list, or ``("", the
    refusal)``. The member is the verified member of the run binding
    (``chat_pin_tools._member``). A shared room binds ``room:<thread>`` as the
    memory id, and ``memory_tools.run_member_address`` refuses it. A run that
    is not solo (:func:`_solo_run`) is refused too (task 4017)."""
    if _pin._on_whatsapp():
        return "", _ON_WHATSAPP
    member = _pin._member()
    if not member:
        return "", _NO_MEMBER
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        memory = _get_memory_user_id() or ""
    except Exception:
        memory = ""
    if run_member_address(memory) is None or not _solo_run():
        return "", _SHARED_ROOM
    return member, ""


def _solo_run() -> bool:
    """True only when the run boundary says this run is the member's own
    solo chat (``executor.run_is_solo``), as ``home_tools._solo_run``. Task
    4017: the chat list is the member's own. A room of one that other members
    can read (visibility ``org`` or ``people``) binds the member's own memory
    id, so only this flag keeps the list out of it. A failed import reads
    False. Never raises."""
    try:
        from orchestrator.executor import run_is_solo

        return run_is_solo() is True
    except Exception:
        return False


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _line(value: Any, width: int = MAX_FIELD_CHARS) -> str:
    """*value* as one line of at most *width* characters, with no hidden
    character. A cut line ends with "..."."""
    text = one_line(strip_hidden(str(value if value is not None else "")))
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="...")


def _block(label: str, rows: Any) -> tuple[str, Any]:
    """``(the framed block, the frame)``: ONE ``UntrustedFrame`` block."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return frame.wrap(label, text), frame


def _hidden(value: str) -> dict[str, Any] | None:
    """The refusal of a name with a character that no person sees, or None.
    The one rule is ``hidden_text``, so an emoji and a Persian word pass."""
    problem = hidden_problem(value, "name")
    return None if problem is None else _refusal(f"{problem} Nothing changed.")


def _chat_id(raw: Any) -> tuple[str, str]:
    """``(the chat id, "")`` or ``("", the refusal)``. Empty means this chat."""
    text = str(raw or "").strip()
    if not text or text.lower() in ("this", "this chat", "current"):
        sid = _pin._session()
        return (sid, "") if sid else ("", _NO_THIS_CHAT)
    if len(text) > MAX_ID_CHARS or not text.isprintable():
        return "", _BAD_ID
    return text, ""


def _q(value: str) -> str:
    """*value* as one path segment."""
    return quote(value, safe="")


# ── The reads ───────────────────────────────────────────────────────────────


async def _list(member: str) -> tuple[list[dict[str, Any]] | None, str]:
    """The rows of the member's chat list, or ``(None, the refusal)``."""
    status, body = await _pin._send("GET", SESSIONS_PATH, member)
    if status == 0:
        return None, _UNREAD
    if status == 403:
        return None, _FORBIDDEN
    if status != 200:
        return None, _NO_ANSWER
    if not isinstance(body, list):
        return None, _SHAPE
    return [r for r in body if isinstance(r, dict) and isinstance(r.get("id"), str)], ""


def _find(rows: list[dict[str, Any]], cid: str) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("id") == cid), None)


async def _topics(member: str) -> tuple[list[dict[str, Any]] | None, str]:
    """The topics of the member, or ``(None, the refusal)``. The route
    answers 404 while ``CHAT_TOPICS_ORGS`` leaves the org out."""
    status, body = await _pin._send("GET", TOPICS_PATH, member)
    if status == 404:
        return None, _TOPICS_OFF
    if status == 0:
        return None, _UNREAD
    if status == 403:
        return None, _FORBIDDEN
    if status != 200:
        return None, _NO_ANSWER
    rows = body.get("topics") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None, _SHAPE
    return [t for t in rows if isinstance(t, dict) and isinstance(t.get("id"), str)], ""


def _snapshot(row: dict[str, Any] | None, *, delete: bool = False) -> tuple[Any, ...]:
    """What a card of a chat must still match when the member answers. A
    delete also holds the count of messages and of people, and the last
    change."""
    if row is None:
        return (None,)
    keys = ["title", "archivedAt", "topicId", "isOwner"]
    if delete:
        keys += ["messageCount", "participantCount", "updatedAt"]
    return tuple(row.get(k) for k in keys)


def _owner(row: dict[str, Any]) -> bool:
    """The owner rule of the dock: a row that does not say ``isOwner: false``
    is the member's own (``SessionPicker.tsx``)."""
    return row.get("isOwner") is not False


def _sharing(row: dict[str, Any]) -> str:
    """How far a chat is shared, from the list alone: ``org`` for a chat that
    each member of the organization can open, ``shared`` for a chat with more
    than one row of people or groups, or "" for a chat of the member alone.
    The count of the list holds the owner's own row and a row of a group, so
    no answer gives a number of people."""
    if str(row.get("visibility") or "private") == "org":
        return "org"
    if str(row.get("visibility") or "private") != "private":
        return "shared"
    return "shared" if int(row.get("participantCount") or 0) > 1 else ""


def _chat_row(r: dict[str, Any], this: str) -> dict[str, Any]:
    """One chat of the list, with the fields of the dock. A title and a topic
    are the text of a member."""
    out: dict[str, Any] = {
        "id": _line(r.get("id"), MAX_ID_CHARS),
        "title": _line(r.get("title") or "New chat"),
        "updated": _line(r.get("updatedAt"), 40),
        "archived": bool(r.get("archivedAt")),
        "pinned": r.get("pinned") is True,
        "shared": _sharing(r) != "",
        "owner": _owner(r),
    }
    if r.get("topicName"):
        out["topic"] = _line(r.get("topicName"), TOPIC_NAME_MAX + 10)
    if r.get("id") == this:
        out["this_chat"] = True
    return out


@annotate(read_only=True, idempotent=True, open_world=False)
async def list_chats(
    what: str = "chats", archived: bool = False, query: str = "",
    offset: int = 0, limit: int = DEFAULT_ROWS,
) -> dict[str, Any]:
    """Read your chat list, newest first, or the topics of your chat list. It changes nothing.

    Args:
        what: chats for the chat list, or topics for the topics.
        archived: True lists only the archived chats.
        query: words that the title of a chat must hold.
        offset: the first row of the page.
        limit: the rows of one page.
    """
    kind = str(what or "chats").strip().lower()
    if kind not in _WHAT:
        return _refusal(_BAD_WHAT)
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    if kind == "topics":
        topics, why = await _topics(member)
        if topics is None:
            return _refusal(why) if why != _TOPICS_OFF else {"ok": True, "answer": _TOPICS_OFF}
        shown = [{"id": _line(t.get("id"), 64), "name": _line(t.get("name"), TOPIC_NAME_MAX + 10),
                  "mine": t.get("mine") is True} for t in topics]
        block, frame = _block("CHAT TOPICS", shown)
        return {"ok": True, "topics": block, "total": len(shown), "note": frame.note(_TOPICS_NOTE)}
    words = one_line(query) if isinstance(query, str) else ""
    if len(words) > MAX_FIELD_CHARS:
        return _refusal(_BAD_FILTER)
    rows, why = await _list(member)
    if rows is None:
        return _refusal(why)
    want_archived = archived is True or (isinstance(archived, str)
                                         and archived.strip().lower() == "true")
    picked = [r for r in rows if bool(r.get("archivedAt")) is want_archived]
    if words:
        needle = words.casefold()
        picked = [r for r in picked if needle in str(r.get("title") or "").casefold()]
    page = paged(picked, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="chats")
    this = _pin._session()
    block, frame = _block("CHAT LIST", [_chat_row(r, this) for r in page.items])
    return {
        "ok": True,
        "chats": block,
        "total": page.total,
        "shown": len(page.items),
        "page": page.line,
        "note": frame.note(_LIST_NOTE),
    }


# ── The cards ───────────────────────────────────────────────────────────────


async def _approved(title: str, detail: str, member: str) -> bool:
    """Show ONE card, bound to *member*, and wait. True only on an approve."""
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        title, detail, non_interactive_default="deny", responder=member)
    return answer is True


def _not_done(act: str) -> dict[str, Any]:
    """The answer of a card that did nothing, from the one outcome table."""
    from acb_skills import ask_tools

    return _refusal(ask_tools.card_not_done_text(_NOT_DONE[act]))


def _shown(value: Any) -> str:
    """A name on a card: one line, in quotes, with no hidden character."""
    return f'"{_line(value or "New chat", 80)}"'


def _route_refusal(status: int, body: Any) -> dict[str, Any]:
    """The plain words of a write that the route refused."""
    if status == 404:
        return _refusal(_GONE, status=404)
    if status in (409, 422):
        detail = _pin._detail(body)
        return _refusal(f"Nothing changed. {detail}" if detail else _NO_ANSWER, status=status)
    if status == 403:
        return _refusal(_FORBIDDEN, status=403)
    if status == 503:
        detail = _pin._detail(body)
        return _refusal(detail or _NO_ANSWER, status=503)
    return _refusal(_NO_ANSWER, status=status)


# ── edit_chat ───────────────────────────────────────────────────────────────


@annotate(read_only=False, idempotent=False, open_world=False)
async def edit_chat(
    action: str, chat_id: str = "", name: str = "", topic_id: str = "",
) -> dict[str, Any]:
    """Rename a chat of your chat list, archive it, restore it, move it into a topic, or make a topic.

    Args:
        action: rename, archive, restore, move or new_topic.
        chat_id: the id of the chat from the chat list. Empty is this chat.
        name: the new name of the chat, or the name of the new topic.
        topic_id: the id of the topic from the topic list, or none for No topic.
    """
    act = str(action or "").strip().lower()
    if act not in _ACTS:
        return _refusal(_BAD_ACT)
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    if act == "new_topic":
        return await _new_topic(member, name)
    cid, bad = _chat_id(chat_id)
    if bad:
        return _refusal(bad)
    clean = ""
    if act == "rename":
        clean = " ".join(str(name or "").split())[:TITLE_MAX]
        if not clean:
            return _refusal(_NO_NAME)
        hidden = _hidden(clean)
        if hidden is not None:
            return hidden
    rows, why = await _list(member)
    if rows is None:
        return _refusal(why)
    row = _find(rows, cid)
    if row is None:
        return _refusal(_NOT_IN_LIST)
    if act == "move":
        return await _move(member, cid, row, topic_id)
    problem = _edit_problem(act, row, clean)
    if problem:
        return _refusal(problem)
    if await _pin._must_ask():
        title, detail = _edit_card(act, row, clean)
        stopped = await _card_and_second_read(act, title, detail, member, cid, row)
        if stopped is not None:
            return stopped
    return await _patch(act, member, cid, clean)


async def _card_and_second_read(act: str, title: str, detail: str, member: str, cid: str,
                                row: dict[str, Any]) -> dict[str, Any] | None:
    """ONE card, then a second read of the chat. None when the write may go,
    else the answer: the card did nothing, or the chat changed."""
    if not await _approved(title, detail, member):
        return _not_done(act)
    again, why = await _list(member)
    if again is None:
        return _refusal(why, decision="approved")
    if _snapshot(_find(again, cid)) != _snapshot(row):
        return _refusal(_CHANGED, decision="approved")
    return None


async def _patch(act: str, member: str, cid: str, clean: str) -> dict[str, Any]:
    """The ONE ``PATCH /chat/sessions/{id}`` of a rename, an archive or a restore."""
    if act == "rename":
        status, body = await _pin._send("PATCH", f"/chat/sessions/{_q(cid)}", member,
                                        {"title": clean})
    else:
        status, body = await _pin._send("PATCH", f"/chat/sessions/{_q(cid)}", member,
                                        {"archived": act == "archive"})
    _log.info("chat_thread_tool.sent", act=act, status=status)
    if status != 200:
        return _route_refusal(status, body)
    if act == "rename":
        block, frame = _block("CHAT NAME", {"title": _line(clean, TITLE_MAX)})
        return {"ok": True, "renamed": True, "chat": block,
                "answer": "Renamed the chat." + _RELOAD, "note": frame.note(_DONE_NOTE)}
    if act == "archive":
        return {"ok": True, "archived": True,
                "answer": "Archived the chat. It shows under Archived." + _RELOAD}
    return {"ok": True, "archived": False,
            "answer": "Restored the chat into your chat list." + _RELOAD}


def _edit_problem(act: str, row: dict[str, Any], clean: str) -> str:
    """The words for an act that the route would refuse or that changes
    nothing, or "". An archive and a restore are for the owner only
    (``_MAY_DELETE_SQL`` of the route)."""
    if act == "rename":
        if not _owner(row):
            return _RENAME_OWNER_ONLY
        return _SAME_NAME if (row.get("title") or "") == clean else ""
    if act == "archive" and row.get("archivedAt"):
        return _ARCHIVED_ALREADY
    if act == "restore" and not row.get("archivedAt"):
        return _NOT_ARCHIVED
    if not _owner(row):
        return _OWNER_ONLY
    return ""


def _edit_card(act: str, row: dict[str, Any], clean: str) -> tuple[str, str]:
    """The title and the detail of the card of an edit."""
    old = _shown(row.get("title"))
    shared = _sharing(row) != ""
    if act == "rename":
        detail = f"From {old} to {_shown(clean)}. Only the name changes."
        if shared:
            detail += " Each person who can open this chat sees the new name."
        return "Rename this chat?", detail
    if act == "archive":
        detail = f"{old} moves under Archived. Its messages and files stay."
        if shared:
            # The archive is a column of the chat (``_patch_session``), so it
            # moves the chat under Archived for each person in it.
            detail += " It moves under Archived for each person who can open it too."
        return "Archive this chat?", detail
    detail = f"{old} goes back into your chat list."
    if shared:
        detail += " It goes back into the list of each person who can open it too."
    return "Restore this chat?", detail


async def _move(member: str, cid: str, row: dict[str, Any], topic_id: Any) -> dict[str, Any]:
    """``PUT /chat/sessions/{id}/topic``: the topic of the member for one chat."""
    raw = str(topic_id or "").strip()
    tid: str | None
    if not raw or raw.lower() in ("none", "no topic", "null"):
        tid = None
    else:
        try:
            tid = str(uuid.UUID(raw))
        except ValueError:
            return _refusal(_BAD_TOPIC)
    if row.get("archivedAt"):
        return _refusal(_ARCHIVED_MOVE)
    topics, why = await _topics(member)
    if topics is None:
        return _refusal(why)
    topic = next((t for t in topics if t.get("id") == tid), None) if tid else None
    if tid and topic is None:
        return _refusal(_NO_SUCH_TOPIC)
    if (row.get("topicId") or None) == tid:
        return _refusal(_SAME_TOPIC)
    if await _pin._must_ask():
        where = f"the topic {_shown(topic.get('name'))}" if topic else "No topic"
        stopped = await _card_and_second_read(
            "move", "Move this chat?",
            f"{_shown(row.get('title'))} moves to {where}. Only your own chat list changes.",
            member, cid, row)
        if stopped is not None:
            return stopped
    status, body = await _pin._send("PUT", f"/chat/sessions/{_q(cid)}/topic", member,
                                    {"topic_id": tid})
    _log.info("chat_thread_tool.sent", act="move", status=status)
    if status != 200:
        return _route_refusal(status, body)
    data = body if isinstance(body, dict) else {}
    block, frame = _block("CHAT TOPIC", {"topic": _line(data.get("topicName") or "No topic",
                                                        TOPIC_NAME_MAX + 10)})
    return {"ok": True, "moved": True, "topic_id": data.get("topicId"), "chat": block,
            "answer": "Moved the chat." + _RELOAD, "note": frame.note(_DONE_NOTE)}


async def _new_topic(member: str, name: Any) -> dict[str, Any]:
    """``POST /chat/topics``: a new private topic of the member (D163.2)."""
    text = str(name or "").strip()
    if not text or len(text) > TOPIC_NAME_MAX:
        return _refusal(_NO_TOPIC_NAME)
    hidden = _hidden(text)
    if hidden is not None:
        return hidden
    if await _pin._must_ask() and not await _approved(
            "Make a topic?", f"A new private topic {_shown(text)}. Only you see it.", member):
        return _not_done("new_topic")
    status, body = await _pin._send("POST", TOPICS_PATH, member, {"name": text})
    _log.info("chat_thread_tool.sent", act="new_topic", status=status)
    if status == 404:
        return _refusal(_TOPICS_OFF, status=404)
    if status not in (200, 201):
        return _route_refusal(status, body)
    made = body.get("topic") if isinstance(body, dict) else None
    if not isinstance(made, dict) or not isinstance(made.get("id"), str):
        return _refusal(_SHAPE)
    block, frame = _block("CHAT TOPIC", {"id": _line(made.get("id"), 64),
                                         "name": _line(made.get("name"), TOPIC_NAME_MAX + 10)})
    return {"ok": True, "made": True, "topic": block,
            "answer": "Made the topic. Move a chat into it with its id.",
            "note": frame.note(_DONE_NOTE)}


# ── delete_chat ─────────────────────────────────────────────────────────────


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_chat(chat_id: str) -> dict[str, Any]:
    """Delete one archived chat of your chat list for good, after one card. Only its owner can delete it.

    Args:
        chat_id: the id of the chat from the chat list.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    raw = str(chat_id or "").strip()
    if not raw:
        return _refusal(_BAD_ID)
    cid, bad = _chat_id(raw)
    if bad:
        return _refusal(bad)
    if cid == _pin._session():
        return _refusal(_DELETE_THIS)
    rows, why = await _list(member)
    if rows is None:
        return _refusal(why)
    row = _find(rows, cid)
    if row is None:
        return _refusal(_NOT_IN_LIST)
    if not _owner(row):
        return _refusal(_DELETE_OWNER_ONLY)
    if not row.get("archivedAt"):
        return _refusal(_ARCHIVE_FIRST)
    detail = (f"{_shown(row.get('title'))} and its messages and files go for good. "
              "You cannot undo it.")
    sharing = _sharing(row)
    if sharing == "org":
        detail += " Each member of the organization who can open it loses it too."
    elif sharing == "shared":
        detail += " Each other person and group that it is shared with loses it too."
    if not await _approved("Delete this chat?", detail, member):
        return _not_done("delete")
    again, why = await _list(member)
    if again is None:
        return _refusal(why, decision="approved")
    if _snapshot(_find(again, cid), delete=True) != _snapshot(row, delete=True):
        return _refusal(_CHANGED, decision="approved")
    status, body = await _pin._send("DELETE", f"/chat/sessions/{_q(cid)}", member)
    _log.info("chat_thread_tool.sent", act="delete", status=status)
    if status not in (200, 204):
        return _route_refusal(status, body)
    return {"ok": True, "deleted": True, "decision": "approved",
            "answer": "Deleted the chat." + _RELOAD}


#: Each tool of the ``chat-threads`` pack, in the order of its skill family.
CHAT_THREAD_TOOLS = (list_chats, edit_chat, delete_chat)
