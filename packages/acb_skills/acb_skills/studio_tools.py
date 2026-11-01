"""The Studio builder tools for Mira (STU-G1 and STU-G2).

Spec: ``project-docs/specs/ai_studio.md`` sections 6.2 and 14 (G1 and G2).

Seven **stateless** tools. No draft store exists in G1 (A3 is Stage 2). The
model holds the manifest in the chat turn and sends it back on each call.

* :func:`studio_draft` gives a minimal valid skeleton from a short brief.
  It makes no model call.
* :func:`studio_patch` applies ``add``, ``replace`` and ``remove`` ops, then
  validates.
* :func:`studio_validate` validates.
* :func:`studio_outline` is a deterministic text with the counts and the names.
  :func:`outline_text` is the pure function behind it, and the plan card of G2
  uses it.
* :func:`studio_list_actions` lists the action catalog.
* :func:`studio_interview` (G2) checks the interview: at most five questions,
  one topic each. It returns the text for ``ask_questions``. It does not wrap
  that tool, because the executor finds ``ask_questions`` by its name.
* :func:`studio_plan` (G2) checks a manifest and shows ONE plan card with
  ``request_confirmation``. The model cannot call ``request_confirmation``,
  so this tool parks on the card itself, as the CRM write tools do. A stop, a
  refusal and a run with no card channel all write nothing. A run with no card
  channel gets the decision ``no_card``, and the plan text stays in ``context``. The tool makes no
  write call, and G1 keeps no store.

**One result shape.** Each manifest tool returns ``{ok, errors, diff,
manifest}``. ``errors`` holds the validator's errors verbatim, as ``path`` and
``message``, up to :data:`MAX_ERRORS` of them. ``manifest`` is present only
when the manifest is valid, so a refusal leaves the model with the manifest it
sent.

**A cap.** A serialized manifest over :data:`MAX_MANIFEST_CHARS` characters is
refused with one sentence, the outline and the size. ``TOOL_RESULT_SPILL`` is
off by default, so the chat would carry the whole text on each call.

**Every result stays under the cap.** The notes of a result (each key but
``manifest``) hold at most :data:`MAX_MANIFEST_CHARS` characters as JSON, and
a manifest comes back only when it is under the cap too. The cuts that hold
it: :data:`MAX_ERRORS` errors and :data:`MAX_DIFF_LINES` diff lines, each
list with one line that counts the rest. Each echoed text is one line with no
control character, and has a length limit. In the worst case, each character
of a cut text costs 2 characters of JSON (a quote or a backslash), and the
limits below hold the sum under the cap with that cost.

**The catalog comes over HTTP.** A package must not import the gateway. The
tools read ``GET /studio/actions`` as the member, with the idiom of
``crm_tools`` (the internal bearer and ``X-User-Email`` from the run
ContextVar). With no member, a tool refuses. A failed read is reported in the
result and the manifest is not called valid, because the checks for unknown
actions and class X could not run.

**No tool takes an org or a member argument** (R5). The tenant is the
member's, bound by the gateway. No tool runs code. Fence:
``tests/unit/test_studio_builder_tools.py``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from acb_common.paging import paged
from acb_common.studio_manifest import STUDIO_VERSION, ManifestError, validate_manifest
from acb_common.studio_patch import PatchError, apply_patch

from acb_skills.tool_annotations import annotate

#: The most characters of one serialized manifest in a tool call (G1 cap).
MAX_MANIFEST_CHARS = 24_000

#: The most action rows one ``studio_list_actions`` answer holds.
MAX_ACTION_ROWS = 30

#: The longest text of a brief that the skeleton keeps as the purpose.
MAX_PURPOSE_CHARS = 300

#: The longest app name that the skeleton takes from the brief. With the
#: purpose and the id, a draft is far under the cap.
MAX_NAME_CHARS = 80

#: The most errors that one result shows. A longer list ends with one line
#: that counts the rest. Worst case: 21 x (25 + 2 x (80 + 300)) = 16,485.
MAX_ERRORS = 20

#: The longest path and the longest message of one shown error.
MAX_ERROR_PATH_CHARS = 80
MAX_ERROR_MESSAGE_CHARS = 300

#: The most diff lines that one result shows, with the same count line, and
#: the longest line. Worst case: 21 x (3 + 2 x 100) = 4,263.
MAX_DIFF_LINES = 20
MAX_DIFF_LINE_CHARS = 100

#: The longest name in the outline, and the longest line of it. A line that
#: cannot hold each name ends with a count of the rest. The outline has seven
#: lines. Worst case: 2 x (7 x 400 + 6) = 5,612. With the errors: 22,097.
MAX_OUTLINE_NAME_CHARS = 80
MAX_OUTLINE_LINE_CHARS = 400

#: The longest action id that a row may hold. No id of the ``uses`` grammar is
#: longer, so a longer id is not a usable action and its row is left out. An
#: id is never cut, because a cut id names no action.
MAX_ACTION_ID_CHARS = 128

#: The longest title, and the longest class, source or connection, of a row.
#: Worst case: 30 x (60 + 2 x (128 + 80 + 3 x 40)) = 21,480.
MAX_ACTION_TITLE_CHARS = 80
MAX_ACTION_TAG_CHARS = 40

#: The topics of the interview (``ai_studio.md`` section 6.2 step 1), and the
#: most questions of one interview. A topic is asked once.
INTERVIEW_TOPICS = ("who", "records", "next", "approver", "systems")
MAX_INTERVIEW_QUESTIONS = 5

#: The most options of one question. Option text is not cut here:
#: ``ask_questions`` cuts the text it shows.
MAX_INTERVIEW_OPTIONS = 6

#: The header of a question that gives none.
_TOPIC_HEADERS = {
    "who": "Who uses it",
    "records": "What it keeps",
    "next": "What happens next",
    "approver": "Who approves",
    "systems": "Other systems",
}

#: The build cost line of the plan card. The cost is not priced yet (G2): no
#: credit estimator exists, and a number here would be invented.
COST_LINE = "Build cost: not priced yet. Credit limits come with a later release."

CATALOG_PATH = "/studio/actions"

_NO_MEMBER = "There is no member for this run, so the Studio tools cannot act."
_SHARED_ROOM = "A shared room cannot use the Studio tools. Ask in your own chat."


# ── The gateway, as the member (the idiom of ``crm_tools``) ─────────────────


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


def _internal_token() -> str:
    from acb_common import get_settings

    settings = get_settings()
    return (
        getattr(settings, "gateway_internal_token", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, refuse."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_SHARED_ROOM)
    return {
        "Authorization": f"Bearer {_internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


async def _read_catalog() -> list[dict[str, Any]]:
    """The rows of ``GET /studio/actions``. Raise :class:`_Refused` with a sentence."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_gateway_url()}{CATALOG_PATH}", headers=headers)
    except httpx.HTTPError as exc:
        raise _Refused(
            f"The action catalog could not be read ({type(exc).__name__}). Try again.") from None
    if resp.status_code == 404:
        raise _Refused("AI Studio is not open for this organization, so the action catalog is off.")
    if resp.status_code in (401, 403):
        raise _Refused("The action catalog is not open to you.")
    if resp.status_code != 200:
        raise _Refused(
            f"The action catalog could not be read (status {resp.status_code}). Try again.")
    try:
        rows = resp.json().get("actions")
    except Exception:
        rows = None
    if not isinstance(rows, list):
        raise _Refused("The action catalog answered in a shape that this tool cannot read.")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)
            and len(r["id"]) <= MAX_ACTION_ID_CHARS]


def _class_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {str(r["id"]): str(r.get("class") or "") for r in rows}


# ── The result shape ────────────────────────────────────────────────────────


def _errors(errors: list[ManifestError]) -> list[dict[str, str]]:
    return [{"path": e.path, "message": e.message} for e in errors]


def _sentence(message: str, path: str = "$") -> list[dict[str, str]]:
    return [{"path": path, "message": message}]


def _clip(value: Any, limit: int) -> str:
    """*value* as text of at most *limit* characters, with no control character.

    A control character becomes a space, so it costs 1 character of JSON and
    not 6. A cut text ends with ``...``."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return "".join(ch if ch >= " " else " " for ch in text)


def _line_of(value: Any, limit: int) -> str:
    """*value* as one line of at most *limit* characters (:func:`_clip`), with
    each run of white space made one space."""
    text = value if isinstance(value, str) else str(value)
    head = text[: limit * 4]  # so a huge text is never split in full
    one = " ".join(head.split())
    if len(head) < len(text):
        one += " ..."
    return _clip(one, limit)


def _shown_errors(errors: list[dict[str, str]]) -> list[dict[str, str]]:
    """The first :data:`MAX_ERRORS` errors, each cut, and one line that counts
    the rest. An error under the limits stays verbatim."""
    shown = [{"path": _clip(e.get("path", "$"), MAX_ERROR_PATH_CHARS),
              "message": _clip(e.get("message", ""), MAX_ERROR_MESSAGE_CHARS)}
             for e in errors[:MAX_ERRORS]]
    rest = len(errors) - len(shown)
    if rest > 0:
        shown.append({"path": "$", "message": (
            f"{rest} more errors are not shown. Fix the errors above, then check again.")})
    return shown


def _shown_diff(diff: list[str]) -> list[str]:
    """The first :data:`MAX_DIFF_LINES` lines of *diff*, each cut, and one line
    that counts the rest."""
    shown = [_line_of(d, MAX_DIFF_LINE_CHARS) for d in diff[:MAX_DIFF_LINES]]
    rest = len(diff) - len(shown)
    if rest > 0:
        shown.append(f"and {rest} more ops")
    return shown


def _result(
    ok: bool, errors: list[dict[str, str]], diff: list[str],
    manifest: dict[str, Any] | None = None, **extra: Any,
) -> dict[str, Any]:
    """THE one shape of a result. It cuts the errors and the diff, so no path
    of a tool can give a list over the cap."""
    out: dict[str, Any] = {"ok": ok, "errors": _shown_errors(errors), "diff": _shown_diff(diff)}
    if ok and manifest is not None:
        out["manifest"] = manifest
    out.update(extra)
    return out


def _size(manifest: Any) -> int:
    return len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), default=repr))


def _too_big(manifest: Any) -> dict[str, Any] | None:
    """The refusal for a manifest over the cap, or None."""
    size = _size(manifest)
    if size <= MAX_MANIFEST_CHARS:
        return None
    sentence = (
        f"The manifest is {size} characters, over the limit of {MAX_MANIFEST_CHARS}. "
        "Split the app into smaller parts.")
    return _result(False, _sentence(sentence), [], size=size,
                   limit=MAX_MANIFEST_CHARS, outline=outline_text(manifest))


def _as_doc(manifest: Any) -> tuple[Any, dict[str, Any] | None]:
    """The manifest as a value. A JSON string is read. A bad string is a refusal."""
    if isinstance(manifest, str):
        try:
            return json.loads(manifest), None
        except ValueError:
            return None, _result(
                False, _sentence("The manifest text is not valid JSON. Send a JSON object."), [])
    return manifest, None


# ── The outline (pure) ──────────────────────────────────────────────────────


def _name(value: Any) -> str:
    """One name of the outline: one line of at most :data:`MAX_OUTLINE_NAME_CHARS`."""
    return _line_of(value, MAX_OUTLINE_NAME_CHARS)


def _names(items: Any, key: str) -> list[str]:
    if not isinstance(items, list):
        return []
    return [_name(i[key]) for i in items if isinstance(i, dict) and isinstance(i.get(key), str)]


def _line(label: str, names: list[str]) -> str:
    """One line of the outline: the label, the count, and the names that fit in
    :data:`MAX_OUTLINE_LINE_CHARS`. The line counts the names that do not fit."""
    head = f"{label} ({len(names)}): "
    if not names:
        return head + "none"
    budget = MAX_OUTLINE_LINE_CHARS - 24  # the room for ", and N more"
    shown: list[str] = []
    used = len(head)
    for name in names:
        add = len(name) + (2 if shown else 0)
        if used + add > budget:
            break
        shown.append(name)
        used += add
    rest = len(names) - len(shown)
    return head + ", ".join(shown) + (f", and {rest} more" if rest else "")


def outline_text(manifest: Any) -> str:
    """A deterministic text of the counts and the names in *manifest*.

    Pure: the same manifest gives the same text. It reads any value and never
    raises, so it also outlines a manifest that is not valid. It has seven
    lines, each of at most :data:`MAX_OUTLINE_LINE_CHARS` characters, also for
    a manifest far over the cap.
    """
    if not isinstance(manifest, dict):
        return "Not a manifest."
    objects = manifest.get("objects")
    objs: list[str] = []
    if isinstance(objects, list):
        for o in objects:
            if isinstance(o, dict) and isinstance(o.get("name"), str):
                count = len(o["fields"]) if isinstance(o.get("fields"), list) else 0
                objs.append(f"{_name(o['name'])} ({count} fields)")
    skill = manifest.get("skill")
    skill_line = "Skill: none"
    if isinstance(skill, dict):
        actions = skill.get("actions")
        n = len(actions) if isinstance(actions, list) else 0
        skill_line = f"Skill: yes ({n} actions)"
    conns = manifest.get("connections")
    conn_names = [_name(c) for c in conns] if isinstance(conns, list) else []
    head = f"App {_name(manifest.get('id', '?'))}: {_name(manifest.get('name', '?'))}"
    return "\n".join([
        head,
        _line("Objects", objs),
        _line("Actions", _names(manifest.get("actions"), "id")),
        _line("Views", _names(manifest.get("views"), "id")),
        _line("Workflows", _names(manifest.get("workflows"), "id")),
        skill_line,
        _line("Connections", conn_names),
    ])


# ── The draft (pure) ────────────────────────────────────────────────────────


def _slug(brief: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", brief.lower()).strip("-")
    words = re.sub(r"^[^a-z]+", "", words)[:40].strip("-")
    return words or "my-app"


def _skeleton(brief: str) -> dict[str, Any]:
    text = " ".join(brief.split())
    title = " ".join(text.split()[:5])[:MAX_NAME_CHARS].strip(" .,;:") or "My app"
    return {
        "studio": STUDIO_VERSION,
        "id": _slug(text),
        "name": title[:1].upper() + title[1:],
        "purpose": text[:MAX_PURPOSE_CHARS] or "To be written.",
        "connections": [],
        "objects": [],
        "actions": [],
        "views": [],
        "workflows": [],
    }


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_draft(brief: str) -> dict[str, Any]:
    """Make a minimal valid app manifest from a short brief. Fill it with studio_patch."""
    if not isinstance(brief, str) or not brief.strip():
        return _result(False, _sentence("Give a short brief of the app."), [])
    manifest = _skeleton(brief)
    errors = validate_manifest(manifest)
    if errors:
        return _result(False, _errors(errors), [])
    return _result(True, [], ["new manifest"], manifest)


async def _checked(manifest: Any, diff: list[str]) -> dict[str, Any]:
    """Validate *manifest* with the catalog, and shape the result."""
    big = _too_big(manifest)
    if big is not None:
        return big
    try:
        catalog = _class_map(await _read_catalog())
    except _Refused as exc:
        # The catalog checks could not run, so the manifest is not called valid.
        structural = _errors(validate_manifest(manifest))
        notice = _sentence(f"{exc} The action checks did not run.")
        return _result(False, [*notice, *structural], diff, catalog_checked=False)
    errors = validate_manifest(manifest, catalog=catalog)
    return _result(not errors, _errors(errors), diff, manifest)


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_validate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Check an app manifest, including its actions. Returns the errors, or the manifest."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    return await _checked(doc, [])


def _diff(ops: list[Any]) -> list[str]:
    return [f"{o.get('op')} {o.get('path')}" if isinstance(o, dict) else "?" for o in ops]


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_patch(manifest: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply JSON Patch ops (add, replace, remove) to a manifest, then check it."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    big = _too_big(doc)
    if big is not None:
        return big
    try:
        patched = apply_patch(doc, ops)
    except PatchError as exc:
        return _result(False, _sentence(str(exc)), [])
    return await _checked(patched, _diff(ops))


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_outline(manifest: dict[str, Any]) -> dict[str, Any]:
    """List the counts and names in a manifest: objects, views, workflows, skill, connections."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    big = _too_big(doc)  # the outline and the size, and no manifest
    if big is not None:
        return big
    errors = validate_manifest(doc)
    return _result(not errors, _errors(errors), [], doc, outline=outline_text(doc),
                   size=_size(doc))


# ── The interview and the plan card (STU-G2) ────────────────────────────────


def _interview_refusal(message: str) -> dict[str, Any]:
    return _result(False, _sentence(message, "$.questions"), [])


def _topic_list(value: Any) -> tuple[list[str], str | None]:
    """The topics in *value*, or a refusal sentence."""
    if value is None:
        return [], None
    if not isinstance(value, list) or any(
            not isinstance(t, str) or t not in INTERVIEW_TOPICS for t in value):
        return [], ("The stated list must hold only these topics: "
                    f"{', '.join(INTERVIEW_TOPICS)}.")
    return list(value), None


def _clean_options(raw: Any, index: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, list):
        return None, f"Question {index} has options that are not a list."
    if len(raw) > MAX_INTERVIEW_OPTIONS:
        return None, (f"Question {index} has more than {MAX_INTERVIEW_OPTIONS} options. "
                      "Use fewer options.")
    out: list[dict[str, Any]] = []
    for o in raw:
        if not isinstance(o, dict) or not isinstance(o.get("label"), str) or not o["label"].strip():
            return None, f"Question {index} has an option with no label."
        row: dict[str, Any] = {"label": o["label"]}
        if isinstance(o.get("description"), str) and o["description"].strip():
            row["description"] = o["description"]
        if o.get("recommended") is True:
            row["recommended"] = True
        out.append(row)
    return out, None


def _clean_question(
    q: Any, i: int, seen: list[str], known: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """One question of the interview, or a refusal sentence. It adds the topic
    of a good question to *seen*."""
    if not isinstance(q, dict):
        return None, f"Question {i} is not an object."
    topic = q.get("topic")
    if not isinstance(topic, str) or topic not in INTERVIEW_TOPICS:
        return None, f"Question {i} needs a topic. Use one of: {', '.join(INTERVIEW_TOPICS)}."
    if topic in seen:
        return None, f"Question {i} repeats the topic {topic}. Ask each topic once."
    if topic in known:
        return None, (f"Question {i} asks about {topic}, and the brief states it already. "
                      "Ask only what the brief does not say.")
    text = q.get("question")
    if not isinstance(text, str) or not text.strip():
        return None, f"Question {i} has no question text."
    options, problem = _clean_options(q.get("options"), i)
    if problem:
        return None, problem
    header = q.get("header")
    entry: dict[str, Any] = {
        "header": header if isinstance(header, str) and header.strip() else _TOPIC_HEADERS[topic],
        "question": text,
    }
    if options:
        entry["options"] = options
    for flag in ("multiSelect", "allowFreeformInput", "required"):
        if isinstance(q.get(flag), bool):
            entry[flag] = q[flag]
    seen.append(topic)
    return entry, None


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_interview(
    questions: list[dict[str, Any]], stated: list[str] | None = None,
) -> dict[str, Any]:
    """Check the interview before you ask it: at most five questions, one topic each.

    Each question has a "topic" (who, records, next, approver or systems), a
    "question", and may have a "header" and "options". Put the topics that the
    brief already states in "stated": a question on one of them is refused.
    Pass the "questions" text of the answer to ask_questions.
    """
    doc, bad = _as_doc(questions)
    if bad is not None:
        return bad
    if isinstance(doc, dict) and "questions" in doc:
        doc = doc["questions"]
    if not isinstance(doc, list) or not doc:
        return _interview_refusal("Send a list of at least one question.")
    if len(doc) > MAX_INTERVIEW_QUESTIONS:
        return _interview_refusal(
            f"An interview holds at most {MAX_INTERVIEW_QUESTIONS} questions. "
            f"You sent {len(doc)}. Keep the ones that matter most.")
    known, problem = _topic_list(stated)
    if problem:
        return _interview_refusal(problem)
    seen: list[str] = []
    cleaned: list[dict[str, Any]] = []
    for i, q in enumerate(doc, start=1):
        entry, problem = _clean_question(q, i, seen, known)
        if problem or entry is None:
            return _interview_refusal(problem or f"Question {i} is not valid.")
        cleaned.append(entry)
    body = json.dumps({"questions": cleaned}, ensure_ascii=False)
    if len(body) > MAX_MANIFEST_CHARS:
        return _interview_refusal("The interview text is too long. Use shorter questions.")
    return _result(True, [], [f"{len(cleaned)} questions"], None, questions=body)


def _count(manifest: dict[str, Any], key: str) -> int:
    value = manifest.get(key)
    return len(value) if isinstance(value, list) else 0


def parts_count(manifest: Any) -> int:
    """The parts of an app: objects, views, workflows, skill and connections.

    Pure. It reads any value and never raises. The plan card shows this count
    and no price, because no credit estimator exists yet (G2)."""
    if not isinstance(manifest, dict):
        return 0
    skill = 1 if isinstance(manifest.get("skill"), dict) else 0
    return (_count(manifest, "objects") + _count(manifest, "views")
            + _count(manifest, "workflows") + skill + _count(manifest, "connections"))


def plan_card_text(manifest: Any) -> str:
    """The text of the plan card: the outline, the count of parts, the cost line.

    Pure and deterministic. It holds the outline (seven lines, each of at most
    :data:`MAX_OUTLINE_LINE_CHARS` characters) and three short lines, so the
    text fits the context limit of the card (4,000 characters). It holds no
    price and no turn count: no number stands next to "credit"."""
    return "\n".join([
        outline_text(manifest),
        f"Parts: {parts_count(manifest)}",
        COST_LINE,
        "Approve to start the build. Stop to build nothing. To change the plan, tell me in chat.",
    ])


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    """Check an app manifest, then show the member ONE plan card and wait.

    The card lists the objects, views, workflows, skill, connections and
    actions. The answer has a "decision": approved, revised (the member wrote
    in the chat), stopped, or no_card (the chat shows no card). After "stopped", write nothing and call no more
    patch. An invalid manifest shows no card. The text of the card is in
    "context", for a chat that cannot show cards.
    """
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    checked = await _checked(doc, [])
    if not checked["ok"]:
        return _result(False, checked["errors"], [])  # no card for an invalid manifest
    title = f"Build {_name(doc.get('name', '?'))}?"
    detail = f"{parts_count(doc)} parts. {COST_LINE}"
    context = plan_card_text(doc)
    from acb_skills import ask_tools

    # The run's own member may answer, and no other (PZ-32). The member is the
    # run's, never an input.
    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", close_on_steer=True,
        responder=_current_user_email() or None)
    if answer is True:
        decision = "approved"
    elif answer is ask_tools.REVISED:
        decision = "revised"
    elif not ask_tools.confirmation_channel_open():
        # A denial AFTER a read of the channel, as ``knowledge_tools`` does: a
        # run with no card shows nothing, and no member pressed Stop.
        decision = "no_card"
    else:
        decision = "stopped"
    return _result(True, [], [], None, title=title, detail=detail, context=context,
                   decision=decision)


def _tag(value: Any, limit: int) -> Any:
    """A text field of a row, cut to *limit* (:func:`_line_of`). None stays None."""
    return None if value is None else _line_of(value, limit)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    """One action row. The id is whole (``_read_catalog`` keeps only an id of at
    most :data:`MAX_ACTION_ID_CHARS`). Each other text is cut."""
    return {
        "id": r.get("id"),
        "title": _tag(r.get("title"), MAX_ACTION_TITLE_CHARS),
        "class": _tag(r.get("class"), MAX_ACTION_TAG_CHARS),
        "source": _tag(r.get("source"), MAX_ACTION_TAG_CHARS),
        "connection": _tag(r.get("connection"), MAX_ACTION_TAG_CHARS),
    }


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_list_actions(query: str = "", offset: int = 0, limit: int = 0) -> dict[str, Any]:
    """List the actions an app may use. The optional query filters by id and title.

    One page holds at most 30 actions. The "page" line names the total and the offset
    of the next page.
    """
    try:
        rows = await _read_catalog()
    except _Refused as exc:
        return _result(False, _sentence(str(exc)), [])
    needle = (query or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in str(r.get("id", "")).lower() or needle in str(r.get("title", "")).lower()]
    page = paged(rows, offset, limit, default=MAX_ACTION_ROWS, maximum=MAX_ACTION_ROWS,
                 noun="actions")
    return {"ok": True, "errors": [], "diff": [], "actions": [_row(r) for r in page.items],
            "total": page.total, "shown": len(page.items), "page": page.line}


#: The seven tools, in the order of the skill family ``studio``.
STUDIO_TOOLS = (
    studio_draft,
    studio_interview,
    studio_list_actions,
    studio_outline,
    studio_patch,
    studio_plan,
    studio_validate,
)

__all__ = [
    "MAX_ACTION_ROWS", "MAX_MANIFEST_CHARS", "STUDIO_TOOLS", "outline_text", "parts_count",
    "plan_card_text", "studio_draft", "studio_interview", "studio_list_actions",
    "studio_outline", "studio_patch", "studio_plan", "studio_validate",
]
