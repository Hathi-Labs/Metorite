"""The member answers one agent question from the chat (WS-55 EA-18d, spec §12.8).

``answer_agent_question`` is a composite of the manifest tool
``post_agent_activity`` (``manifest.COMPOSITE``), so it is class B. It writes ONE
``prompt`` on a run that awaits the member. The server decides who may answer:
``POST /projects/agent-runs/{id}/activities`` runs ``may_answer_run`` (D-EA-14),
and a refusal comes back as one sentence. The tool holds no copy of that rule.

**The fence.** ``test_projects_agent_requests_tools.py`` forbids every module of
the skill to name an answer route, except THIS file, for the routes under a run
only. No module names the archive approval route. A typed archive request is
answered by the run's member alone, on the task card (EA3-40, D-OP-13), so this
tool refuses it before any card. It does not reach the archive approval route,
because that route is class X.

The tool reads the run first (a GET) so the card names the question. It writes
nothing before the member confirms.
"""

from __future__ import annotations

from typing import Any

from acb_common.no_person import NO_PERSON_TEXT, no_person_present

from skill_projects.client import GatewayRefusal, data, get, post, uuid_of
from skill_projects.refusals import REFUSED
from skill_projects.writes import CANCELLED, _confirm, _fields_block

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


#: The longest answer. The route allows ``MAX_BODY`` and clips nothing.
ANSWER_BODY_MAX = 4000

NOT_AWAITING = (
    "No question waits on this run, so there is nothing to answer. "
    "agent_questions lists the questions that wait."
)

ARCHIVE_ON_CARD = (
    "This is a request to archive a task. Only the run's member approves or declines it, "
    "on the task card. Give the member the link. Nothing was changed."
)


def _options_of(question: dict[str, Any]) -> list[dict[str, Any]]:
    meta = question.get("signal_metadata") or {}
    return [o for o in meta.get("options") or [] if isinstance(o, dict)]


def _is_archive_request(question: dict[str, Any]) -> bool:
    request = (question.get("content_metadata") or {}).get("request") or {}
    return bool(request.get("kind"))


def _pick_refusal(question: dict[str, Any], options: list[dict[str, Any]], picked: list[str]) -> str:
    """The sentence that refuses a bad ``selected``, or an empty string."""
    if not picked:
        return ""
    ids = {str(o.get("id")) for o in options}
    if not ids:
        return "This question has no options, so selected does not apply. Answer in text."
    if not set(picked) <= ids:
        return f"selected must be option ids from: {', '.join(sorted(ids))}. Nothing was changed."
    if len(picked) > 1 and not (question.get("signal_metadata") or {}).get("allow_multiple"):
        return "This question takes one option. Nothing was changed."
    return ""


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False, card_write=True)
async def answer_agent_question(run_id: str, answer: str = "", selected: str = "") -> str:
    """Answer ONE question that an agent put and that waits on the member. run_id
    comes from agent_questions. Send what the member said, in answer (free text),
    selected (the option id, from agent_questions, or several ids split by a
    comma), or both. Never invent an answer. A card shows the question and the
    answer, and nothing is sent until the member confirms. The server decides if
    this member may answer, and a refusal comes back in one sentence. A request
    to archive a task is answered only on the task card, and this tool refuses
    it."""
    if no_person_present():
        return f"{REFUSED} {NO_PERSON_TEXT} Nothing was changed."
    rid = uuid_of(run_id, "run_id")
    text = str(answer or "").strip()
    picked = [p.strip() for p in str(selected or "").split(",") if p.strip()]
    if not text and not picked:
        return "An answer needs text, an option id, or both. Nothing was changed."
    if len(text) > ANSWER_BODY_MAX:
        return f"An answer is at most {ANSWER_BODY_MAX} characters. Nothing was changed."
    payload = (await get(f"/projects/agent-runs/{rid}")) or {}
    run = payload.get("run") or {}
    question = run.get("open_elicitation") or {}
    if run.get("status") != "awaiting" or not question:
        return NOT_AWAITING
    if _is_archive_request(question):
        return ARCHIVE_ON_CARD
    options = _options_of(question)
    if refusal := _pick_refusal(question, options, picked):
        return refusal
    body: dict[str, Any] = {
        "type": "prompt",
        "idempotency_key": f"chat-answer-{question.get('id')}",
    }
    if text:
        body["body"] = text
    if picked:
        body["selected"] = picked
    asks = data(str((question.get("content") or {}).get("body") or "")[:400])
    parts: list[str] = []
    if picked:
        labels = {str(o.get("id")): str(o.get("label") or o.get("id")) for o in options}
        parts.append(", ".join(data(labels[p]) for p in picked))
    if text:
        parts.append(data(text))
    card: dict[str, Any] = {"question": asks, "answer": " · ".join(parts)}
    if not await _confirm(
        title="Answer this agent question?",
        detail=asks,
        context=_fields_block(
            card, lead="The answer goes on the task as a comment that everyone who can open "
                       "the task reads. The agent then reads it."),
    ):
        return CANCELLED
    try:
        written = await post(f"/projects/agent-runs/{rid}/activities", body)
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 403:
            return f"{REFUSED} {exc} Nothing was changed."
        raise
    created = (written or {}).get("created", True)
    done = "Answered" if created else "Already answered, with the same answer"
    return f"{done}: {asks}\n  run_id: {rid}"
