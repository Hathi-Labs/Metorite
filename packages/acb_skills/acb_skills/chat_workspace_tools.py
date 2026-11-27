"""The files of THIS chat, from the chat (PAR-11 part c, Metorite task 4014).

Spec: ``project-docs/specs/chat_parity.md``, wave P-7. The Files panel of a
chat (``ArtifactSidebar.tsx`` and ``ArtifactViewerModal.tsx``) lists the files
of the chat, shows the history of a file, saves the text of a file, deletes a
file, and moves an upload into Agent Data. Four tools do the same from the
chat:

* :func:`list_chat_files` reads the file list (``GET /agent/workspace/{id}``)
  or the history of one file (``GET /agent/workspace/{id}/history``). It only
  reads.
* :func:`edit_chat_file` saves new text in a text file that the list holds
  (``PUT /agent/workspace/{id}/file``), as the Save of the viewer does.
* :func:`promote_chat_file` moves one upload of ``inputs/`` into
  ``agent-data/`` (``POST /agent/workspace/{id}/promote``), as "Promote to
  Agent Data" does.
* :func:`delete_chat_file` deletes one file (``DELETE
  /agent/workspace/{id}/file``), as the Delete of the panel does.

The rules:

* **The route decides.** Each tool sends the route over HTTP as the member,
  with the internal bearer and ``X-User-Email``, as ``chat_pin_tools`` does.
  So the room rule (``_refuse_unless_room``), the thread folders (H-227), the
  skill owner rule (WS-43v), the shared clone rule and the tenant of the
  member apply. No tool opens a database session or writes a Redis key.
* **No chat id, no member and no org from the model (R5).** The chat is the
  session of the run binding (``chat_pin_tools._session``). The member is the
  verified member of the run (``chat_pin_tools._member``). A path is only a key
  of the list of this chat: a write reads the list first, and a path that the
  list does not hold gets plain words and no request.
* **The acts of the panel, and no more.** A save is for a text file that the
  list holds, in ``inputs/``, ``outputs/`` or ``agent-data/``, with text only
  (``encoding`` ``utf-8``). A new file is ``write_artifact``'s job. A promote is
  for an upload of ``inputs/``, and it sends ``path`` alone, as the panel does.
* **One card before each write, bound to the member.** Each write ALWAYS shows
  ONE card first (``responder``), and only an approve sends. A delete card
  names the file. A write reads the list before its card, reads it again after
  the approve, and sends nothing when the file changed. A save shows the whole
  new text on its card, so a text that does not fit on one card gets plain
  words and no card.
* **The card of a save shows the text as the save writes it.** The card of the
  web app reads a body of ``key: value`` lines as fields. It drops an id line,
  joins numbered keys and renames keys (``ConfirmationCard.parseCardBody``).
  So the body of a save card (:func:`_card_text`) starts with two lines of the
  tool that are no field. The card then draws the body as one text, each
  character as sent. A carriage return that is not before a new line, and a
  Unicode line or paragraph separator, draw as no line break. So a text with
  one of them gets plain words and no card. The shared fixture
  ``tests/fixtures/chat_file_card_cases.json`` pins the body and the drawing.
* **Not in a shared room, not on WhatsApp, not in a run with no chat.** The
  answer of a shared room shows to each person in it. So each tool refuses
  there with no request.
* **Text of other people is data.** A file name and a history row can come from
  an upload, from an agent, or from a colleague in Agent Data. Each result puts
  them in ONE ``UntrustedFrame`` block. A path with a character that no person
  sees is refused before any request (``hidden_text``).
* **Dark.** The tools are the pack ``chat-files``, owned by ``org-admin``. It is
  in no ``FOLLOWS`` entry, so it is off until the owner names it in the pack
  list of the box (``tool_packs.packs_on``). ``org-admin`` is in
  ``egress.HELD_WRITE_OWNERS``, so a held chat holds off the three writes when
  ``HELD_CARD_WRITES_ORGS`` names the org. No write is on
  ``HELD_CARD_WRITE_TOOLS``.

Fences: ``tests/unit/test_chat_workspace_tools.py`` and the R8 suite
``tests/unit/test_chat_workspace_tools_r8.py``.
"""

from __future__ import annotations

import json
import re
import textwrap
from typing import Any
from urllib.parse import quote

import httpx
from acb_common import get_logger
from acb_common.card_body import CARD_CLIP, one_line
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills import chat_pin_tools as _pin
from acb_skills.company_search_tools import _gateway_url
from acb_skills.hidden_text import hidden_problem, strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "CHAT_WORKSPACE_TOOLS",
    "delete_chat_file",
    "edit_chat_file",
    "list_chat_files",
    "promote_chat_file",
]

_log = get_logger("acb_skills.chat_workspace_tools")

#: The longest wait for a route, in seconds.
TIMEOUT_S = 15.0
#: The folders of a chat that the panel shows and changes. A copy of
#: ``gateway.routes.workspace._VISIBLE_WORKSPACE_DIRS``. The fence holds the two
#: equal, because a package must not import the gateway.
VISIBLE_DIRS = ("agent-data", "inputs", "outputs")
#: The folder of an upload, and the folder that a promote moves it into.
INPUTS_DIR = "inputs"
AGENT_DATA_DIR = "agent-data"
#: The file types that the viewer edits as text (``ArtifactViewerModal.tsx``:
#: markdown, html, react, code, text and report). A save of any other type gets
#: plain words and no card.
TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".html", ".htm", ".csv", ".tsv", ".json", ".yaml",
    ".yml", ".xml", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".toml",
    ".ini", ".cfg", ".log", ".sql",
})
#: The room on a save card for the two head lines of :func:`_card_text`.
_HEAD_ROOM = 400
#: The longest new text of a save: the head lines and the whole text must fit
#: on one card (``acb_common.card_body.CARD_CLIP``, the clip of
#: ``ask_tools.request_confirmation``).
CONTENT_MAX = CARD_CLIP - _HEAD_ROOM
#: The characters that the card draws as no line break, though a file reads
#: them as one: a carriage return that is not before a new line, U+2028 and
#: U+2029.
_FALSE_BREAK = re.compile("\r(?!\n)|[\u2028\u2029]")
#: The rows of one page, by default and at most.
DEFAULT_ROWS = 25
MAX_ROWS = 50
#: The longest path that a tool takes, and the longest text of one value.
MAX_PATH_CHARS = 400
MAX_FIELD_CHARS = 200

_WHAT = ("files", "history")

_ON_WHATSAPP = ("Nothing was done. The files of a chat belong to the Metorite web app. Open "
                "the chat there to see its files.")
_NO_MEMBER = ("Nothing was done. Metorite could not confirm who asked in this chat. Use the "
              "Files panel of the chat.")
_SHARED_ROOM = ("Nothing was done. Each person in a shared chat sees the answer. Use the Files "
                "panel of the chat, or ask in your own chat.")
_NO_CHAT = ("Nothing was done. This run has no chat, so it has no files. Open the chat in the "
            "Metorite web app.")
_BAD_WHAT = "what is files or history. Nothing was read."
_BAD_PATH = ("Send the path of one file as the file list shows it, for example "
             "outputs/report.md. Nothing was done.")
_OUTSIDE = ("The Files panel shows and changes only the folders inputs, outputs and "
            "agent-data. Nothing was done.")
_NO_HISTORY_PATH = "Send the path of one file to read its history. Nothing was read."
_NOT_IN_LIST = ("The file list of this chat holds no file with that path. Read the file list "
                "for the paths. Nothing changed.")
_NOT_TEXT = ("The chat saves only a text file, such as .md, .txt, .html, .csv or .json. "
             "Open the file in the Files panel to change it. Nothing changed.")
_BAD_CONTENT = "Send the new text of the file as text. Nothing changed."
_TOO_LONG = ("The new text is too long to show in full on one card ({n} characters, and a "
             "card holds {max}). Make the text shorter, or change the file in the Files panel. "
             "Nothing changed.")
_FALSE_BREAK_TEXT = ("The new text holds a carriage return that is not before a new line, or a "
                    "Unicode line separator. The card cannot show it as the file reads it. "
                    "Use a plain new line. Nothing changed.")
_NOT_INPUT = ("Only an upload in inputs can move to Agent Data. Read the file list for the "
              "paths. Nothing changed.")
_CHANGED = ("The file changed after the card showed, so nothing was sent. Read the file list "
            "again, then ask again.")
_GONE = "The file is not in the file list of this chat now. Nothing changed."
_UNREAD = "The file list did not answer. Nothing was done. Try again."
_SHAPE = "The file list answered in a shape that this tool cannot read. Nothing was done."
_FORBIDDEN = ("Nothing was done. Your account cannot change the files of this chat. "
              "{detail}")
_REFUSED = "Nothing was done. {detail}"
_NOT_FOUND = "Nothing was done. The file or the chat was not found."
_NO_ANSWER = "Nothing was done. The files of the chat did not answer. Try again."

_FILES_NOTE = ("The block holds the files of this chat. An upload, an agent or a colleague "
               "in Agent Data can name a file.")
_HISTORY_NOTE = "The block holds the versions of one file, newest first."
_DONE_NOTE = "The block holds the file that the act changed."
_NO_FILES = "This chat has no files yet."
_NO_ROWS = "This file has no history yet."
_RELOAD = " The Files panel shows the change when it loads again."

#: The act of a card that did nothing, for ``ask_tools.card_not_done_text``.
_NOT_DONE = {
    "edit": "The file keeps its text.",
    "promote": "The file stays in inputs.",
    "delete": "The file was not deleted.",
}


# ── The run and its member ──────────────────────────────────────────────────


def _member_and_chat() -> tuple[str, str, str]:
    """``(member, chat, "")`` for a run that may use the files of its chat, or
    ``("", "", the refusal)``.

    The member is the verified member of the run binding
    (``chat_pin_tools._member``). A shared room binds ``room:<thread>`` as the
    memory id, so a memory id with no address is a shared room. The chat is the
    session of the run binding, and a batch run has none."""
    if _pin._on_whatsapp():
        return "", "", _ON_WHATSAPP
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        memory = _get_memory_user_id() or ""
    except Exception:
        memory = ""
    if memory and "@" not in memory:
        return "", "", _SHARED_ROOM
    member = _pin._member()
    if not member or not memory:
        return "", "", _NO_MEMBER
    sid = _pin._session()
    if not sid:
        return "", "", _NO_CHAT
    return member, sid, ""


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _q(value: str) -> str:
    """*value* as one path segment."""
    return quote(value, safe="")


def _line(value: Any, width: int = MAX_FIELD_CHARS) -> str:
    """*value* as one line of at most *width* characters, with no hidden
    character. A cut line ends with "..."."""
    text = one_line(strip_hidden(str(value if value is not None else "")))
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="...")


def _framed(label: str, rows: Any, key: str, note: str, **extra: Any) -> dict[str, Any]:
    """An answer that holds *rows* as JSON in ONE ``UntrustedFrame`` block,
    with the note of that frame, the idiom of ``notes_read_tools``."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return {"ok": True, **extra, key: frame.wrap(label, text), "note": frame.note(note)}


def _path(raw: Any, *, panel: bool = True) -> tuple[str, str]:
    """``(the relative path, "")`` or ``("", the refusal)``.

    A path is one printable line, with no hidden character, no empty part, no
    ``.`` and no ``..`` part. With *panel* (each write), its first part is a
    folder that the panel changes, and the route holds the same rule. A
    history read takes a path in any folder of the list."""
    if not isinstance(raw, str):
        return "", _BAD_PATH
    text = raw.strip().replace("\\", "/")
    # A leading "/" is no path of the list. The tool refuses it, and never
    # strips it, so the card names the path that the model sent.
    if not text or text.startswith("/") or len(text) > MAX_PATH_CHARS or not text.isprintable():
        return "", _BAD_PATH
    problem = hidden_problem(text, "path")
    if problem is not None:
        return "", f"{problem} Nothing was done."
    parts = text.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "", _BAD_PATH
    if panel and (len(parts) < 2 or parts[0] not in VISIBLE_DIRS):
        return "", _OUTSIDE
    return text, ""


def _suffix(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


# ── The gateway, as the member ──────────────────────────────────────────────


def _transport() -> httpx.AsyncBaseTransport | None:
    """The transport of the request. None is the network. A test returns a
    mock transport or an ASGI transport here."""
    return None


async def _send(method: str, path: str, member: str, *,
                params: dict[str, str] | None = None, body: Any = None) -> tuple[int, Any]:
    """Send ONE request of a workspace route as *member*.

    The headers are the internal bearer, ``X-User-Email`` and the content
    type, and nothing else. *params* is the query of a file route, and *body*
    the JSON body of a write. Returns the status and the JSON body. A fault of
    the request gives status 0. Never raises.

    The pin sender (``chat_pin_tools._send``) takes no query and no body, so
    this module has its own sender with the same headers."""
    headers = {
        **internal_auth_headers(),
        "X-User-Email": member,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(base_url=_gateway_url(), transport=_transport(),
                                     timeout=TIMEOUT_S) as client:
            resp = await client.request(method, path, headers=headers, params=params,
                                        json=body)
    except Exception as exc:
        _log.warning("chat_workspace_tool.request_failed", error=type(exc).__name__)
        return 0, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return int(resp.status_code), data


def _failed(status: int, body: Any) -> str:
    """The sentence of a route that did not do the act."""
    detail = _pin._detail(body)
    if status == 0:
        return _UNREAD
    if status == 404:
        return _NOT_FOUND
    if status == 403:
        return _FORBIDDEN.format(detail=detail).strip()
    if status in (400, 409, 413, 422) and detail:
        return _REFUSED.format(detail=detail)
    return _NO_ANSWER


async def _files(member: str, sid: str) -> tuple[list[dict[str, Any]] | None, str]:
    """The files of the chat, or ``(None, the refusal)``. A room that the
    member cannot read answers an empty list, as a chat with no files does."""
    status, body = await _send("GET", f"/agent/workspace/{_q(sid)}", member)
    if status != 200:
        return None, _failed(status, body)
    rows = body.get("files") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None, _SHAPE
    return [r for r in rows
            if isinstance(r, dict) and isinstance(r.get("path"), str) and not r.get("is_dir")], ""


def _find(rows: list[dict[str, Any]], rel: str) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("path") == rel), None)


def _snapshot(row: dict[str, Any] | None) -> tuple[Any, ...]:
    """What a card of a file must still match when the member answers."""
    if row is None:
        return (None,)
    return tuple(row.get(k) for k in ("path", "size", "modified_at", "mime_type"))


def _file_row(r: dict[str, Any]) -> dict[str, Any]:
    """One file of the list, with the fields of the panel. The root of the
    workspace (an absolute path on the server) is never shown."""
    path = _line(r.get("path"), MAX_PATH_CHARS)
    return {
        "path": path,
        "folder": path.split("/", 1)[0] if "/" in path else "",
        "size": int(r.get("size") or 0),
        "modified": _line(r.get("modified_at"), 40),
        "type": _line(r.get("mime_type"), 80),
    }


def _history_row(r: dict[str, Any]) -> dict[str, Any]:
    """One version of a file. The run, the session and the actor are left out."""
    return {
        "path": _line(r.get("path"), MAX_PATH_CHARS),
        "action": _line(r.get("action"), 20),
        "size": int(r.get("size") or 0),
        "when": _line(r.get("created_at"), 40),
    }


def _card_text(content: str, old_size: int) -> str:
    """The body of a save card: two head lines, then *content* as it is.

    No head line holds a colon, so the card of the web app reads no field in
    the body, and it draws the body as one text (``parseCardBody``). The card
    and ``request_confirmation`` strip the end of the body, so the second line
    counts the white space at the end of the text."""
    size = len(content.encode("utf-8"))
    lines = content.count("\n") + 1 if content else 0
    head = ("Below these two lines is the whole new text of the file, as the save writes it. "
            "It replaces all of the text.")
    counts = (f"The file has {old_size} bytes now. The new text has {size} bytes, "
              f"{len(content)} characters and {lines} lines, to the end of this card.")
    tail = len(content) - len(content.rstrip())
    if tail:
        counts += (f" After its last character that shows, the text has {tail} more "
                   "white space characters, such as a new line.")
    return f"{head}\n{counts}\n{content}"


async def _approved(title: str, detail: str, member: str, context: str = "") -> bool:
    """Show ONE card, bound to *member*, and wait. True only on an approve."""
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", responder=member)
    return answer is True


def _not_done(act: str) -> dict[str, Any]:
    from acb_skills import ask_tools

    return _refusal(ask_tools.card_not_done_text(_NOT_DONE[act]))


async def _still(member: str, sid: str, rel: str, before: tuple[Any, ...],
                 other: str = "", other_before: tuple[Any, ...] = (None,)) -> str:
    """"" when the file (and *other*, for a promote) is as the card showed it,
    else the refusal. It reads the list again after the approve."""
    rows, why = await _files(member, sid)
    if rows is None:
        return why
    row = _find(rows, rel)
    if row is None:
        return _GONE
    if _snapshot(row) != before:
        return _CHANGED
    if other and _snapshot(_find(rows, other)) != other_before:
        return _CHANGED
    return ""


def _done(lead: str, entry: Any, fallback: str) -> dict[str, Any]:
    """The answer of a write that the route took. The path is framed."""
    path = entry.get("path") if isinstance(entry, dict) else None
    shown: dict[str, Any] = {"path": _line(path or fallback, MAX_PATH_CHARS)}
    if isinstance(entry, dict) and isinstance(entry.get("size"), int):
        shown["size"] = entry["size"]
    return _framed("CHAT FILE", shown, "file", _DONE_NOTE, done=lead + _RELOAD)


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def list_chat_files(what: str = "files", path: str = "", offset: int = 0,
                          limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """Read the files of this chat, or the history of one file. It changes nothing.

    Call it first, to get the path of a file before you change it.

    Args:
        what: "files" lists the files of this chat, as the Files panel shows
            them. "history" lists the versions of one file, newest first.
        path: The path of one file, as the file list shows it. Only for "history".
        offset: The first row of the page.
        limit: The rows of one page, at most 50.
    """
    if what not in _WHAT:
        return _refusal(_BAD_WHAT)
    member, sid, why = _member_and_chat()
    if why:
        return _refusal(why)
    if what == "files":
        rows, why = await _files(member, sid)
        if rows is None:
            return _refusal(why)
        if not rows:
            return {"ok": True, "total": 0, "note": _NO_FILES}
        page = paged(rows, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="files")
        return _framed("CHAT FILES", [_file_row(r) for r in page.items], "files",
                       _FILES_NOTE, total=page.total, page=page.line)
    if not str(path or "").strip():
        return _refusal(_NO_HISTORY_PATH)
    rel, why = _path(path, panel=False)
    if why:
        return _refusal(why)
    status, body = await _send("GET", f"/agent/workspace/{_q(sid)}/history", member,
                               params={"path": rel})
    if status != 200:
        return _refusal(_failed(status, body))
    found = body.get("history") if isinstance(body, dict) else None
    if not isinstance(found, list):
        return _refusal(_SHAPE)
    versions = [r for r in found if isinstance(r, dict)]
    if not versions:
        return {"ok": True, "total": 0, "note": _NO_ROWS}
    page = paged(versions, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS,
                 noun="versions")
    return _framed("FILE HISTORY", [_history_row(r) for r in page.items], "history",
                   _HISTORY_NOTE, total=page.total, page=page.line)


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def edit_chat_file(path: str, content: str) -> dict[str, Any]:
    """Save new text in one text file of this chat, after ONE card.

    It replaces the whole text of a file that the file list holds, as the Save
    of the file viewer does. To make a new file, use write_artifact.

    Args:
        path: The path of the file, as the file list shows it.
        content: The whole new text of the file.
    """
    member, sid, why = _member_and_chat()
    if why:
        return _refusal(why)
    rel, why = _path(path)
    if why:
        return _refusal(why)
    if _suffix(rel) not in TEXT_SUFFIXES:
        return _refusal(_NOT_TEXT)
    if not isinstance(content, str):
        return _refusal(_BAD_CONTENT)
    problem = hidden_problem(content, "content")
    if problem is not None:
        return _refusal(f"{problem} Nothing changed.")
    if _FALSE_BREAK.search(content):
        return _refusal(_FALSE_BREAK_TEXT)
    if len(content) > CONTENT_MAX:
        return _refusal(_TOO_LONG.format(n=len(content), max=CONTENT_MAX))
    rows, why = await _files(member, sid)
    if rows is None:
        return _refusal(why)
    row = _find(rows, rel)
    if row is None:
        return _refusal(_NOT_IN_LIST)
    before = _snapshot(row)
    card = _card_text(content, int(row.get("size") or 0))
    if len(card.strip()) > CARD_CLIP:
        # Fail closed: a card that the clip cuts does not show the whole text.
        return _refusal(_TOO_LONG.format(n=len(content), max=CONTENT_MAX))
    detail = f"{_line(rel, MAX_PATH_CHARS)}: the new text replaces all of the text."
    if not await _approved("Save this file?", detail, member, context=card):
        return _not_done("edit")
    why = await _still(member, sid, rel, before)
    if why:
        return _refusal(why)
    status, body = await _send("PUT", f"/agent/workspace/{_q(sid)}/file", member,
                               params={"path": rel},
                               body={"content": content, "encoding": "utf-8"})
    _log.info("chat_workspace_tool.sent", act="edit", status=status)
    if status != 200:
        return _refusal(_failed(status, body))
    return _done("Saved the new text of the file.", body, rel)


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def promote_chat_file(path: str) -> dict[str, Any]:
    """Move one upload of this chat from inputs into Agent Data, after ONE card.

    Agent Data is the lasting memory of the assistant: later chats read it.
    It does what "Promote to Agent Data" of the Files panel does.

    Args:
        path: The path of the upload in inputs, as the file list shows it.
    """
    member, sid, why = _member_and_chat()
    if why:
        return _refusal(why)
    rel, why = _path(path)
    if why:
        return _refusal(why)
    if rel.split("/", 1)[0] != INPUTS_DIR:
        return _refusal(_NOT_INPUT)
    rows, why = await _files(member, sid)
    if rows is None:
        return _refusal(why)
    row = _find(rows, rel)
    if row is None:
        return _refusal(_NOT_IN_LIST)
    dest = f"{AGENT_DATA_DIR}/{rel.rsplit('/', 1)[-1]}"
    taken = _find(rows, dest)
    before, dest_before = _snapshot(row), _snapshot(taken)
    detail = (f"{_line(rel, MAX_PATH_CHARS)} moves to {_line(dest, MAX_PATH_CHARS)}, and "
              "leaves inputs. Agent Data is the lasting memory of this assistant, so later "
              "chats read it. In an assistant that your organization shares, the chats of "
              "other members read it too.")
    if taken is not None:
        detail += " It replaces the file of the same name in Agent Data."
    if not await _approved("Move this file to Agent Data?", detail, member):
        return _not_done("promote")
    why = await _still(member, sid, rel, before, other=dest, other_before=dest_before)
    if why:
        return _refusal(why)
    status, body = await _send("POST", f"/agent/workspace/{_q(sid)}/promote", member,
                               body={"path": rel})
    _log.info("chat_workspace_tool.sent", act="promote", status=status)
    if status != 200:
        return _refusal(_failed(status, body))
    return _done("Moved the file to Agent Data.", body, dest)


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_chat_file(path: str) -> dict[str, Any]:
    """Delete one file of this chat, after ONE card that names the file.

    Delete only when the member asks. It does what the Delete of the Files
    panel does, and it cannot be undone from the chat.

    Args:
        path: The path of the file, as the file list shows it.
    """
    member, sid, why = _member_and_chat()
    if why:
        return _refusal(why)
    rel, why = _path(path)
    if why:
        return _refusal(why)
    rows, why = await _files(member, sid)
    if rows is None:
        return _refusal(why)
    row = _find(rows, rel)
    if row is None:
        return _refusal(_NOT_IN_LIST)
    before = _snapshot(row)
    detail = (f"{_line(rel, MAX_PATH_CHARS)} ({int(row.get('size') or 0)} bytes) leaves this "
              "chat. This cannot be undone.")
    if not await _approved("Delete this file?", detail, member):
        return _not_done("delete")
    why = await _still(member, sid, rel, before)
    if why:
        return _refusal(why)
    status, body = await _send("DELETE", f"/agent/workspace/{_q(sid)}/file", member,
                               params={"path": rel})
    _log.info("chat_workspace_tool.sent", act="delete", status=status)
    if status != 200 or not (isinstance(body, dict) and body.get("deleted") is True):
        return _refusal(_failed(status, body) if status != 200 else _SHAPE)
    return _done("Deleted the file.", {"path": rel}, rel)


#: The four tools of the pack ``chat-files``, in the order of the family.
CHAT_WORKSPACE_TOOLS = (list_chat_files, edit_chat_file, promote_chat_file, delete_chat_file)
