"""POST /shell/intent — the command bar's tier 2, the coordinator (NS-4b).

Spec: ``project-docs/specs/navigation_shell.md`` §6.4, §6.5, §6.6 and NS-4b.
Owner ask, 2026-10-07: *"a high-level coordination AI that enables us to
quickly go to the appropriate app and workflow depending on our query."*

What it does, in two steps:

1. **Pick the job.** One typed choice through ``acb_llm.decide``: which of the
   jobs THIS member can open does the sentence ask for, or is it a question
   for the assistant? ``decide`` runs on the Console Router, which bills it.
2. **Fill the job** (when the owner has turned completion routing on). One
   routed completion reads the job's fields out of the sentence, such as the
   recipient and the subject of an email. Each filled field is a suggestion.
   The form opens with it, and the member checks it and saves (§6.4 rule 2).

The rules it keeps, each with its fence in ``tests/unit/test_shell_intent.py``:

* **The model sees only what the member can open** (§6.4 rule 1). The server
  builds the choice from ITS job list, filtered by the member's features. A job
  list from the client is never read.
* **Every model call is a billed call.** ``decide`` and
  ``completion_on_router`` both go through the Console. There is no local
  litellm path here, so nothing is spent unbilled (H-171).
* **Out of credits is a state, not an error** (§6.5). The answer is
  ``paused``, and the bar says so in one line. Tiers 0 and 1 keep working.
* **A repeat is free** (§6.5). The same words, at the same scope, from the
  same member, within five minutes, come from the cache, which is keyed
  through the tenant-prefix wrapper (R5c).
* **Off is off.** ``COMMAND_BAR_AI`` is read at request time, default off.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.shell.search import router
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: How long one answer is kept (§6.5, "within 5 minutes").
CACHE_SECONDS = 300
#: Below this confidence, the bar hands the words to the assistant instead.
MIN_CONFIDENCE = 0.5
#: The words the model may read.
MAX_WORDS_CHARS = 400
#: A filled field is a suggestion, never a document.
MAX_FIELD_CHARS = 200
#: The one line the bar shows when the credits run out (§6.5).
PAUSED_LINE = "AI suggestions are paused. Ask an admin to add credits."
#: The tier the fill step names. The Router picks the model (D32.7).
FILL_TIER = "tier-fast"
#: Seconds for the pick and for the fill. ⚠️ A server-side bound: Uvicorn does
#: not cancel a handler when the browser gives up, so without it a slow
#: Console held a worker for up to 120 s (security review of NS-4b,
#: 2026-10-08). Email bounds `decide` the same way (`ON_BOUND_S`).
PICK_TIMEOUT_S = 3.0
FILL_TIMEOUT_S = 3.0


def ai_on() -> bool:
    """``COMMAND_BAR_AI``, read on every request, so a flip needs a restart only."""
    return os.environ.get("COMMAND_BAR_AI", "").strip().lower() in {"1", "true", "on"}


@dataclass(frozen=True)
class Job:
    id: str
    label: str
    #: What the job is for, in the words the model compares against.
    meaning: str
    #: The feature that opens it, or None for a job every member holds.
    feature: str | None
    href: str
    #: Field name → what to put there. Empty for a job with no form.
    fields: dict[str, str] = field(default_factory=dict)


#: The jobs, in step with `workbench/control_plane/src/lib/shell/registry.ts`
#: `JOBS` (ids, links and owning apps). `test_shell_intent.py` fails if the two
#: drift, because a job the bar shows and the coordinator cannot pick, or the
#: other way round, is two answers to "what can I do here".
JOBS: tuple[Job, ...] = (
    Job("capture", "New task", "add a task, a to-do or a reminder for themselves",
        "tasks", "/tasks?do=capture",
        {"title": "the task itself, in the member's own words, without 'remind me to'"}),
    Job("compose", "Write an email", "write, send or reply to an email",
        "email", "/email?do=compose",
        {"to": "who the email goes to: a name or an email address",
         "subject": "a short subject line for the email"}),
    Job("plan-day", "Plan my day", "plan, schedule or look at their day, their calendar or their agenda",
        "tasks", "/calendar"),
    Job("find-person", "Find a colleague", "find a colleague, or who knows or does something",
        "people", "/people"),
    Job("edit-profile", "Update my profile", "change their own profile, skills, CV or working hours",
        None, "/people/me"),
    Job("appearance", "Change how Metorite looks", "change the theme, dark or light mode, density or accent",
        None, "/settings/appearance"),
)

#: The option that means "not a job": the assistant takes it.
ASK = "ask"


def held_jobs(user: UserContext) -> list[Job]:
    """The jobs this member can open. Only these reach the model (§6.4 rule 1)."""
    return [j for j in JOBS if j.feature is None or user.has_permission(f"feature:{j.feature}")]


class IntentRequest(BaseModel):
    """The words, and the app the member is in. Nothing else is read.

    ⚠️ ``extra="ignore"``: a client that sends its own job list is not refused,
    and its list is never read. The server's list is the only one.
    """

    model_config = ConfigDict(extra="ignore")
    q: str = Field(default="", max_length=2000)
    scope: str | None = Field(default=None, max_length=100)


def _handoff(words: str) -> dict[str, Any]:
    return {"kind": "handoff", "href": "/chat?" + urlencode({"q": words})}


def _job_answer(job: Job, filled: dict[str, str]) -> dict[str, Any]:
    params = {f"fill.{k}": v for k, v in filled.items()}
    joiner = "&" if "?" in job.href else "?"
    href = job.href + (joiner + urlencode(params) if params else "")
    return {"kind": "job", "job": job.id, "label": job.label, "href": href, "filled": filled}


async def _pick(user: UserContext, words: str, scope: str | None, jobs: list[Job]):
    """The job the words ask for, or ASK. Raises DecideUnavailable when it cannot say."""
    from acb_llm import ChoiceAnswer, ChoiceQuestion, decide
    from acb_llm.routed import run_attribution

    # Who to bill, from the request's own binding, as the email features do
    # (`decide_features.py`). Never a hard-coded "proven": the internal service
    # caller is `system:internal`, which is no member at all.
    attribution = dict(run_attribution())
    attribution["member"] = user.email
    attribution["member_proven"] = True
    criteria = {j.id: f"{j.label}: {j.meaning}" for j in jobs}
    criteria[ASK] = "anything else: a question, a search, or a request none of the jobs above does"
    decision = await decide(
        {"request": words, "where": scope or "anywhere"},
        {"job": ChoiceQuestion(
            instructions=(
                "A member of a company typed this into the search box of their work app. "
                "Which ONE job does it ask for? Choose 'ask' unless a job clearly fits."
            ),
            criteria=criteria,
        )},
        member=attribution["member"],
        member_proven=attribution["member_proven"],
        module_slug="shell",
    )
    answer = decision["job"]
    # A reply of another shape is no decision. The assistant takes the words.
    return answer if isinstance(answer, ChoiceAnswer) else None


async def _fill(job: Job, words: str) -> dict[str, str]:
    """The job's fields, read from the words. Empty when routing is off or unsure."""
    if not job.fields:
        return {}
    from acb_llm.routed import RoutedRefusal, completion_on_router, routing_is_on

    if not routing_is_on():
        # The owner has not turned billed completions on (H-69). The job opens
        # with an empty form, which is still the right job.
        return {}
    keys = ", ".join(f'"{k}" ({v})' for k, v in job.fields.items())
    try:
        resp, _model = await completion_on_router(
            tier=FILL_TIER,
            messages=[
                {"role": "system", "content": (
                    "Read the request and return ONE JSON object with these keys: "
                    f"{keys}. Use only words from the request. Leave out a key you "
                    "cannot fill. Return the JSON object and nothing else.")},
                {"role": "user", "content": words},
            ],
            max_tokens=200,
            temperature=0.0,
            source="shell",
        )
    except RoutedRefusal:
        return {}
    except Exception:
        # ⚠️ Any other failure, an outage above all, is NO fill and never a
        # 500. The pick is already paid for, so the job opens with an empty
        # form and the answer is cached (review, P1). A raise here sent the
        # member nothing and billed the pick again on the next pause.
        logger.info("shell.intent fill failed")
        return {}
    try:
        text = resp.choices[0].message.content or ""
        start, end = text.find("{"), text.rfind("}")
        raw = json.loads(text[start:end + 1]) if 0 <= start < end else {}
    except Exception:
        return {}
    out: dict[str, str] = {}
    for name in job.fields:
        value = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(value, str) and value.strip():
            out[name] = " ".join(value.split())[:MAX_FIELD_CHARS]
    return out


def _cache_key(user: UserContext, words: str, scope: str | None) -> str:
    """The member, the scope and the words. Never the words alone (§6.5)."""
    raw = "\x1f".join([(user.email or "").lower(), scope or "", words.lower()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


async def _cached(user: UserContext, digest: str) -> dict[str, Any] | None:
    org = getattr(user, "organization_id", None)
    if not org:
        return None
    try:
        from acb_common.tenant_redis import get_tenant_redis, key, organization_scope

        with organization_scope(str(org)):
            raw = await get_tenant_redis().get(key("shell-intent", digest))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _remember(user: UserContext, digest: str, answer: dict[str, Any]) -> None:
    org = getattr(user, "organization_id", None)
    if not org:
        return
    try:
        from acb_common.tenant_redis import get_tenant_redis, key, organization_scope

        with organization_scope(str(org)):
            await get_tenant_redis().setex(key("shell-intent", digest), CACHE_SECONDS, json.dumps(answer))
    except Exception:
        return


@router.post("/intent")
async def shell_intent(
    body: IntentRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What the words ask for: a job to open (filled), or a hand-off to the assistant."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not ai_on():
        return {"kind": "off"}
    # A member is a person with an address. The internal service caller
    # (`system:internal`) is not one, and nobody is billed for its words.
    if "@" not in user.email:
        return {"kind": "unavailable"}
    words = " ".join((body.q or "").split())[:MAX_WORDS_CHARS]
    if len(words.split()) < 2:
        return {"kind": "none"}

    digest = _cache_key(user, words, body.scope)
    hit = await _cached(user, digest)
    if hit is not None:
        return hit

    jobs = held_jobs(user)
    from acb_llm import DecideError, DecideUnavailable

    try:
        answer = await asyncio.wait_for(_pick(user, words, body.scope, jobs), PICK_TIMEOUT_S)
    except TimeoutError:
        logger.info("shell.intent pick timed out")
        return {"kind": "unavailable"}
    except DecideUnavailable as exc:
        if exc.reason == "insufficient_credits":
            return {"kind": "paused", "message": PAUSED_LINE}
        logger.info("shell.intent unavailable", extra={"reason": exc.reason})
        return {"kind": "unavailable"}
    except DecideError as exc:
        logger.warning("shell.intent refused", extra={"error": type(exc).__name__})
        return {"kind": "unavailable"}

    chosen = next((j for j in jobs if answer and j.id == answer.choice), None)
    unsure = bool(answer) and answer.confidence is not None and answer.confidence < MIN_CONFIDENCE
    if chosen is None or unsure:
        result = _handoff(words)
    else:
        try:
            filled = await asyncio.wait_for(_fill(chosen, words), FILL_TIMEOUT_S)
        except TimeoutError:
            filled = {}
        result = _job_answer(chosen, filled)
    await _remember(user, digest, result)
    return result
