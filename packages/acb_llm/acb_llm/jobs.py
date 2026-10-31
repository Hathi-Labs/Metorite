"""The job of an in-product AI call, and the settings each job gets (task 1128).

Each in-product AI call names the KIND of work it does. This module turns the
job into the settings of the request. It is the ONE place the policy lives.
Do not choose thinking or a cap floor for a call anywhere else.

==========  =============  ========  =========================================
Job         Default tier   Thinking  Use it for
==========  =============  ========  =========================================
structured  tier-fast      off       Classify, label, yes or no, pick, extract
                                     or fill a small JSON. Keep a small cap.
writing     tier-balanced  off       Prose that a person reads: a draft, a
                                     recap, a summary, a digest, a style text.
                                     Use ``tier-powerful`` where quality
                                     matters most. The cap fits the longest
                                     good answer.
judgement   tier-powerful  on        Plan, decide among many options, or make
                                     rules. The cap is at least
                                     :data:`JUDGEMENT_MIN_TOKENS`, so the
                                     thinking and the answer both fit.
==========  =============  ========  =========================================

Why the jobs exist
------------------
DeepSeek V4 Pro, and very likely V4.1 Flash, THINK before they answer. The
vendor counts the thinking tokens against ``max_tokens``. With a small cap the
model spends the whole cap on thinking and returns empty content. Production
showed it: the day planner stopped at 1200 of 1200 tokens on 8 of 8 calls,
the CRM digest at 800 of 800 on 6 of 6. A job that needs no reasoning asks
for none, and a job that needs it gets a cap that holds it.

A member or an env setting can choose the TIER of a call (``task_models``,
``NOTES_*_MODEL``, ``SUGGESTION_MODEL``). The job keeps that tier and still
turns thinking off, so any tier works for a structured or a writing job.

How a call names its job
------------------------
``acompletion_with_fallback(..., job="structured")``, ``complete(...,
job="writing")`` and ``completion_on_router(..., job=...)`` read it. The two
``_llm_json`` helpers (``gateway/routes/email/core.py`` and
``gateway/routes/notes/summaries.py``) pass it through. A caller that sends
``thinking`` or ``reasoning_effort`` itself keeps its own value.

Thinking off, and the vendors that refuse it
---------------------------------------------
Measured on litellm 1.103.0 (``uv.lock``), with ``get_optional_params`` and
``drop_params=False``, which is the Console's setting. A "refuse" is
``UnsupportedParamsError``, a 400, before any vendor call:

* **DeepSeek** (every model): ``thinking={"type": "disabled"}`` goes out as
  it is. ``reasoning_effort="none"`` maps to the same thing. This is the fix
  that ``email/automation/replyzero.py::_STATUS_THINKING`` proved.
* **OpenRouter**: a reasoning model takes both. A model that litellm does
  not mark as reasoning (``openai/gpt-4o``, ``deepseek/deepseek-chat``)
  refuses both.
* **Anthropic**: Claude 4 and later take ``thinking`` as it is. An older
  model (``claude-3-5-haiku``) refuses both.
* **OpenAI**: every model refuses ``thinking``. A reasoning model takes
  ``reasoning_effort``, but gpt-5 knows no ``"none"``, so it is not safe.
* **Gemini**: 2.5 takes ``thinking`` and sends an empty ``thinkingConfig``,
  which changes nothing. ``reasoning_effort="none"`` sends a budget of 0,
  which 2.5 Pro refuses at the vendor. 2.0 refuses both.
* **Groq**: every model refuses ``thinking``. Llama refuses both.
* **aimlapi**: litellm's ``aiml`` refuses ``thinking``. The Console binds
  ``aimlapi/...`` to a native handler or to the embed door, never to litellm
  chat, so a chat step there is not served by litellm at all.

**What this module chose.** A job sends ``thinking={"type": "disabled"}``
only, never ``reasoning_effort``, because ``"none"`` is not safe on OpenAI or
Gemini. The send is safe in each place it can go:

* Off the Router, :func:`params_for_model` drops each reasoning parameter
  that litellm says the resolved model does not take.
* On the Router, the gateway does not know the model, because the Console's
  ``tier_binding`` chain picks it, and a chain may mix vendors. So the
  Console drops each reasoning parameter that a STEP's model does not take
  (``customer_console/reasoning.py::reasoning_params_for``), with the same
  litellm rule. One step can take it and the next one not.

A judgement job sends NO thinking parameter. The vendor default thinks on
DeepSeek V4. ``{"type": "enabled"}`` is not safe, because Anthropic refuses
it without a ``budget_tokens``.

The two seam guards
-------------------
* :func:`ensure_json_word`: a provider answers 400 to JSON mode when no
  message says "json". The ``email.sender_pin`` prompt did not, and it failed
  42 times in 7 days. The seam adds one sentence to the system message.
* :func:`note_empty_answer`: the seam returned an empty answer as a success,
  so nothing saw the failures above. It logs ``acb_llm.empty_answer`` once
  for each empty answer. It never raises, so each caller keeps its own
  fail-closed rule.

Fence (R7): ``tests/unit/test_llm_jobs.py``. It walks the gateway, the
orchestrator and the ``acb_*`` packages, and fails on a model call with a cap
under 2000 that names no job and sends no ``thinking`` or
``reasoning_effort``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from acb_common import get_logger

__all__ = [
    "JOBS",
    "JSON_ASK",
    "JUDGEMENT_MIN_TOKENS",
    "REASONING_PARAMS",
    "THINKING_OFF",
    "JobPolicy",
    "apply_job",
    "default_tier",
    "ensure_json_word",
    "is_empty_answer",
    "job_policy",
    "model_takes",
    "note_empty_answer",
    "params_for_model",
]

_log = get_logger("acb_llm.jobs")

#: The smallest cap of a judgement job. The thinking and the answer share it.
JUDGEMENT_MIN_TOKENS: Final = 6000

#: The request value that turns thinking off. litellm sends it to DeepSeek
#: and to Anthropic as it is (see the module docstring).
THINKING_OFF: Final[dict[str, str]] = {"type": "disabled"}

#: The request parameters that control thinking. A vendor that does not take
#: one answers 400, so each is dropped for such a model.
REASONING_PARAMS: Final = ("thinking", "reasoning_effort")

#: The sentence that :func:`ensure_json_word` adds to the system message.
JSON_ASK: Final = "Reply with one valid JSON object."


@dataclass(frozen=True)
class JobPolicy:
    """The settings of one job. See the table in the module docstring."""

    name: str
    tier: str
    thinking: bool
    min_tokens: int = 0


#: The job vocabulary. A new job is a decision, so add it here and nowhere else.
JOBS: Final[dict[str, JobPolicy]] = {
    "structured": JobPolicy("structured", "tier-fast", thinking=False),
    "writing": JobPolicy("writing", "tier-balanced", thinking=False),
    "judgement": JobPolicy(
        "judgement", "tier-powerful", thinking=True,
        min_tokens=JUDGEMENT_MIN_TOKENS),
}


def job_policy(job: str) -> JobPolicy:
    """The policy of *job*. Raises ``ValueError`` for a name not in :data:`JOBS`.

    A job is a code constant, so an unknown name is a typo. The fence checks
    each literal, so the raise is a last guard and not a path.
    """
    try:
        return JOBS[job]
    except KeyError:
        raise ValueError(
            f"unknown AI job {job!r}; use one of {', '.join(sorted(JOBS))}"
        ) from None


def default_tier(job: str) -> str:
    """The tier of *job* when nothing else chooses one."""
    return job_policy(job).tier


def apply_job(
    job: str | None, *, max_tokens: int, extra: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """The cap and the request options of a call that names *job*.

    Returns a NEW dict, and never changes *extra*. A thinking parameter that
    the caller sends itself wins. A job of ``None`` changes nothing. The
    function is idempotent, so a seam may apply it twice.
    """
    if job is None:
        return max_tokens, extra
    policy = job_policy(job)
    out = dict(extra)
    if not policy.thinking and not any(out.get(k) is not None for k in REASONING_PARAMS):
        out["thinking"] = dict(THINKING_OFF)
    if policy.min_tokens and max_tokens < policy.min_tokens:
        max_tokens = policy.min_tokens
    return max_tokens, out


def model_takes(model: str, param: str) -> bool:
    """Whether litellm sends *param* to *model* without a 400.

    *model* is a litellm id, such as ``deepseek/deepseek-v4-pro``. A model
    that litellm cannot place reads as False, so the parameter is dropped.
    The Console keeps the same rule for a chain step
    (``customer_console/reasoning.py``), because it cannot import this package.
    """
    try:
        import litellm

        name, provider, _key, _base = litellm.get_llm_provider(model)
        supported = litellm.get_supported_openai_params(
            model=name, custom_llm_provider=provider) or []
    except Exception:  # a model that litellm cannot place takes nothing
        return False
    return param in supported


def params_for_model(extra: dict[str, Any], model: str) -> dict[str, Any]:
    """*extra* with no reasoning parameter that *model* does not take.

    For the path that calls litellm directly, where the model is known. The
    Router path leaves this to the Console, which knows each step's model.
    Returns *extra* itself when nothing is dropped.
    """
    drop = [k for k in REASONING_PARAMS if k in extra and not model_takes(model, k)]
    if not drop:
        return extra
    return {k: v for k, v in extra.items() if k not in drop}


def _says_json(content: Any) -> bool:
    """Whether a message content holds the word "json", in any case."""
    if isinstance(content, str):
        return "json" in content.lower()
    if isinstance(content, list):
        for part in content:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and "json" in text.lower():
                return True
    return False


def ensure_json_word(
    messages: list[dict[str, Any]], response_format: Any,
) -> list[dict[str, Any]]:
    """*messages* that say "json" when the request asks for JSON mode.

    A provider answers 400 to ``response_format={"type": "json_object"}``
    when no message holds the word. This adds :data:`JSON_ASK` to the system
    message, or puts a system message first when there is none. Returns
    *messages* itself when nothing changes, and a new list when it does.
    """
    if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
        return messages
    if any(isinstance(m, dict) and _says_json(m.get("content")) for m in messages):
        return messages
    out = list(messages)
    for i, message in enumerate(out):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            joined = f"{content}\n\n{JSON_ASK}" if content.strip() else JSON_ASK
            out[i] = {**message, "content": joined}
            return out
        if isinstance(content, list):
            out[i] = {**message, "content": [*content, {"type": "text", "text": JSON_ASK}]}
            return out
    return [{"role": "system", "content": JSON_ASK}, *out]


def _field(obj: Any, name: str) -> Any:
    """One field of a mapping or of an object. None when it is absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def is_empty_answer(resp: Any) -> bool:
    """Whether *resp* holds no answer: no text and no tool call.

    A response with no choice is empty too. Whitespace is no text.
    """
    choices = _field(resp, "choices") or []
    if not choices:
        return True
    message = _field(choices[0], "message")
    if _field(message, "tool_calls"):
        return False
    content = _field(message, "content")
    return not (isinstance(content, str) and content.strip())


def note_empty_answer(
    resp: Any, *, model: str, job: str | None = None, max_tokens: int | None = None,
) -> bool:
    """Log ``acb_llm.empty_answer`` when *resp* holds no answer. Never raises.

    Returns True when the answer is empty. The line holds the agent, the
    model that answered, ``finish_reason``, the completion tokens and the
    reasoning tokens. It holds no prompt and no reply text. The caller keeps
    its own rule for an empty answer.
    """
    try:
        if not is_empty_answer(resp):
            return False
        choices = _field(resp, "choices") or []
        finish = _field(choices[0], "finish_reason") if choices else None
        usage = _field(resp, "usage")
        details = _field(usage, "completion_tokens_details")
        try:
            from acb_llm.routed import run_attribution

            agent = run_attribution().get("agent")
        except Exception:  # attribution is best-effort
            agent = None
        _log.warning(
            "acb_llm.empty_answer",
            agent=agent,
            model=str(_field(resp, "model") or model or ""),
            finish_reason=finish,
            completion_tokens=_field(usage, "completion_tokens"),
            reasoning_tokens=_field(details, "reasoning_tokens"),
            max_tokens=max_tokens,
            job=job,
        )
        return True
    except Exception:  # a log never breaks a model call
        return False
