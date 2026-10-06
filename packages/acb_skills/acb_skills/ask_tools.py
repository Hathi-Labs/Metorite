"""HITL elicitation tool — agent asks the user clarifying questions mid-stream.

Provides ``ask_questions`` which mirrors VS Code Copilot Chat's
``vscode_askQuestions`` tool.  The agent pauses execution, the Control Plane
renders an interactive card with structured questions, and the user's answers
are injected as the next user message so the agent can continue with context.

Design (VS Code parity)
-----------------------
- Accepts an array of question objects, each with a ``header``, ``question``
  text, optional ``options`` (single/multi-select), and optional freeform input.
- One question can be marked ``recommended`` to highlight the default choice.
- The tool emits a ``CUSTOM`` AG-UI event (``elicitation_requested``) into the
  active SSE queue; the frontend renders an ``ElicitationCard`` inline.
- The agent MUST stop after calling this tool — the user's response will
  arrive as the next chat message.

Usage by agents::

    await ask_questions(json.dumps({
        "questions": [
            {
                "header": "Target Environment",
                "question": "Should I deploy to staging or production?",
                "options": [
                    {"label": "Staging", "recommended": true},
                    {"label": "Production"}
                ]
            }
        ]
    }))
"""
from __future__ import annotations

import json as _json

# Tools that PARK the turn waiting on a human. When a run's last tool is one of
# these, the turn ending with no closing assistant text is CORRECT, not a
# failure: the card these render IS the output, and (as the module docstring
# says) "the agent MUST stop after calling this tool".
#
# Anything reasoning about "the agent produced no text" must consult this set
# first, or it will report a normal question as a truncated answer — and tell
# the user to say "continue" when the right move is to answer the question.
#
# ``ask_user`` is the GitHub Copilot SDK's NATIVE elicitation tool (not defined
# in this module); it is listed because it parks the turn identically.
HITL_BLOCKING_TOOLS: frozenset[str] = frozenset({
    "ask_questions",
    "ask_user",
    "request_confirmation",
    # emit_generative_ui blocks only when the spec sets ``hitl: true``
    # (generative_ui_2 Phase 1) — but listing it unconditionally is correct
    # for every consumer of this set: the per-tool-timeout exemption is
    # harmless for the instant non-blocking emit, and a turn that ends on a
    # rendered UI card with no closing text is a legitimate ending (the card
    # IS the output), exactly like the other card tools above.
    "emit_generative_ui",
})


def is_hitl_blocking_tool(name: str) -> bool:
    """True if *name* is a tool that parks the turn awaiting user input."""
    return (name or "").strip().lower() in HITL_BLOCKING_TOOLS


async def ask_questions(questions: str) -> str:
    """Ask the user one or more clarifying questions before proceeding.

    The questions are rendered as interactive cards in the chat UI.  The user
    can select options, type freeform answers, or both.  Their answers will
    be sent as the next chat message for you to process.

    **Use this tool when:**
    - You need to disambiguate between multiple possible interpretations
    - The user's request is missing a required parameter
    - You want the user to choose from a known set of options
    - A decision has security, cost, or irreversibility implications

    **Do NOT use for:**
    - Simple yes/no confirmations (use your best judgment)
    - Questions you can answer yourself with a web search or tool call

    **After calling this tool, STOP and wait.**  The user's response will
    arrive as the next message in the conversation.

    Args:
        questions: JSON string. ``header`` and ``question`` are required;
            ``options`` is optional (each needs ``label``);
            ``allowFreeformInput`` defaults to true only when there are no
            options. Shape:
            {
              "questions": [
                {
                  "header": "Short label (max 50 chars)",
                  "question": "The full question text (max 200 chars)",
                  "multiSelect": false,
                  "allowFreeformInput": true,
                  "options": [
                    {"label": "Option A", "description": "What this means",
                     "recommended": true},
                    {"label": "Option B"}
                  ]
                }
              ]
            }

    Returns:
        ``"Questions displayed to the user. Waiting for response."``
        The agent MUST stop after receiving this and let the user answer.
    """
    try:
        data = _json.loads(questions)
    except (_json.JSONDecodeError, TypeError):
        return (
            "Error: questions must be valid JSON, e.g. "
            '\'{"questions":[{"header":"Confirm","question":"Proceed?"}]}\''
        )

    if not isinstance(data, dict):
        return "Error: questions must be a JSON object with a 'questions' key"

    qs = data.get("questions", [])
    if not isinstance(qs, list) or len(qs) == 0:
        return "Error: 'questions' must be a non-empty array"

    cleaned: list[dict] = []
    for i, q in enumerate(qs):
        if not isinstance(q, dict):
            return f"Error: question {i} is not an object"
        header = str(q.get("header", f"Question {i + 1}")).strip()[:50]
        question = str(q.get("question", "")).strip()[:200]
        if not question:
            return f"Error: question {i} has no question text"

        opts_raw = q.get("options", [])
        options: list[dict] = []
        if isinstance(opts_raw, list):
            for o in opts_raw:
                if isinstance(o, dict) and o.get("label"):
                    options.append({
                        "label": str(o["label"]).strip()[:100],
                        "description": str(o.get("description", "")).strip()[:200] or None,
                        "recommended": bool(o.get("recommended", False)),
                    })

        has_options = len(options) > 0
        cleaned.append({
            "header": header,
            "question": question,
            "multiSelect": bool(q.get("multiSelect", False)),
            "allowFreeformInput": bool(q.get(
                "allowFreeformInput", not has_options,
            )),
            "options": options if has_options else None,
        })

    # ── Emit elicitation event ──────────────────────────────────────────
    # Two paths, unified on the same _pending_user_input mechanism as the
    # Copilot SDK's native ask_user tool:
    #
    # Path A — MAF Tier 2 (blocking):  When _active_run_queue is set we
    #   are running inside the executor's instrumented batch path.  We
    #   create a Future, park on it, and return the user's answer directly
    #   as the tool result.  The agent truly blocks — same reliability as
    #   the Copilot SDK's native ask_user.  The frontend POSTs to
    #   /api/agent/respond-input to resolve the Future.
    #
    # Path B — Copilot SDK / standalone (non-blocking):  The elicitation
    #   card renders and the answer arrives as the next chat message.
    #   The agent must self-regulate (stop after the tool call).
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_run_queue,
            _pending_user_input,
        )
        queue = _active_run_queue.get(None)
    except Exception:  # noqa: BLE001
        queue = None

    if queue is not None:
        # ── Path A: blocking Future (MAF Tier 2) ─────────────────────
        import asyncio as _asyncio
        import uuid as _uuid

        _request_id = _uuid.uuid4().hex
        _loop = _asyncio.get_running_loop()
        _fut: "_asyncio.Future[dict[str, object]]" = _loop.create_future()
        _pending_user_input[_request_id] = _fut

        await queue.put({
            "type": "CUSTOM",
            "name": "elicitation_requested",
            "value": {"questions": cleaned, "request_id": _request_id},
        })

        try:
            # Heartbeating wait (audit R3): touches the relay ACTIVE flag
            # between 60s slices so a long park doesn't lapse the stream.
            from orchestrator.executor import wait_user_future  # noqa: PLC0415
            _result = await wait_user_future(_fut, 3600)
        except _asyncio.TimeoutError:
            _pending_user_input.pop(_request_id, None)
            return "User did not respond in time."

        _answer = str(_result.get("answer", "") or "").strip()
        if not _answer:
            return "User provided an empty response."
        return f"User response: {_answer}"

    # ── Path B: Copilot SDK / standalone ──────────────────────────────
    # Two sub-paths:
    #
    # B1 — Executor-bridged blocking (preferred):  When the executor
    #   detected this ask_questions call and pre-created a Future in
    #   _pending_user_input (signalled via _active_elicitation_request_id
    #   ContextVar), we block on that Future.  The user's answer resolves
    #   the Future; we return the answer as the tool result; the LLM
    #   continues and produces text.  This prevents the "chat dies without
    #   output" bug that occurred when Path B returned immediately.
    #
    # B2 — Legacy non-blocking (fallback):  When no Future was pre-created
    #   (standalone use or executor bridge unavailable), emit the CUSTOM
    #   event (if queue available) and return immediately.  The answer
    #   arrives as the next chat message.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_elicitation_request_id,
            _pending_user_input,
        )
        _req_id = _active_elicitation_request_id.get(None)
        # ── Briefly poll for the executor bridge ──────────────────
        # The executor may set this ContextVar concurrently with tool
        # execution. When it IS visible (native-MAF, and Copilot when the
        # context happens to carry over) B1 is the preferred path — its Future is
        # keyed to the tool_call_id for correct cleanup. But on the Copilot SDK
        # the tool runs in a thread-hopped context that usually RESETS this
        # ContextVar, so it stays None; a short poll then falls through to the
        # thread-hop-safe Path C below (resolve_relay_thread_id) rather than
        # stalling. 0.5s is ample — if it's going to appear it appears at once.
        if _req_id is None:
            import asyncio as _asyncio
            _deadline = _asyncio.get_running_loop().time() + 0.5
            while _req_id is None:
                await _asyncio.sleep(0.05)
                _req_id = _active_elicitation_request_id.get(None)
                if _asyncio.get_running_loop().time() >= _deadline:
                    break
        if _req_id:
            _fut = _pending_user_input.get(_req_id)
            if _fut is not None:
                # ── B1: blocking bridge ──────────────────────────
                import asyncio as _asyncio
                try:
                    from orchestrator.executor import (  # noqa: PLC0415
                        wait_user_future,
                    )
                    _result = await wait_user_future(_fut, 3600)
                except _asyncio.TimeoutError:
                    return "User did not respond in time."
                _answer = str(
                    _result.get("answer", "") or "").strip()
                if not _answer:
                    return "User provided an empty response."
                return f"User response: {_answer}"
    except Exception:  # noqa: BLE001
        pass

    # ── Path C: self-contained blocking (universal fallback) ──────────
    # Works when neither _active_run_queue (Tier 2) nor
    # _active_elicitation_request_id (Tier 1.5) is set — covers
    # Tier 1 MAF AG-UI streaming and standalone calls.  Pushes the
    # elicitation event directly to the Redis relay and blocks on
    # a self-created Future until the user answers.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _pending_user_input,
            _push_sse_to_stream,
            resolve_relay_thread_id,
        )
        # resolve_relay_thread_id survives the Copilot SDK thread hop (the raw
        # _stream_relay_thread_id ContextVar does NOT) — so this blocking path now
        # engages for BOTH Copilot-SDK and native-MAF agents.
        _tid = resolve_relay_thread_id()
        if _tid:
            import asyncio as _asyncio
            import uuid as _uuid

            _request_id = _uuid.uuid4().hex
            _loop = _asyncio.get_running_loop()
            _fut: "_asyncio.Future[dict[str, object]]" = (
                _loop.create_future())
            _pending_user_input[_request_id] = _fut

            _payload = _json.dumps({
                "type": "CUSTOM",
                "name": "elicitation_requested",
                "value": {
                    "questions": cleaned,
                    "request_id": _request_id,
                },
            })
            _line = f"data: {_payload}\n\n"
            await _push_sse_to_stream(_tid, _line)

            try:
                from orchestrator.executor import (  # noqa: PLC0415
                    wait_user_future,
                )
                _result = await wait_user_future(_fut, 3600, thread_id=_tid)
            except _asyncio.TimeoutError:
                _pending_user_input.pop(_request_id, None)
                return "User did not respond in time."
            finally:
                _pending_user_input.pop(_request_id, None)

            _answer = str(
                _result.get("answer", "") or "").strip()
            if not _answer:
                return "User provided an empty response."
            return f"User response: {_answer}"
    except Exception:  # noqa: BLE001
        pass

    # ── B2: legacy non-blocking (last resort) ─────────────────────────
    try:
        from orchestrator.executor import _active_run_queue  # noqa: PLC0415
        queue_b = _active_run_queue.get(None)
        if queue_b is not None:
            await queue_b.put({
                "type": "CUSTOM",
                "name": "elicitation_requested",
                "value": {"questions": cleaned},
            })
    except Exception:  # noqa: BLE001
        pass

    return (
        "Questions displayed to the user. "
        "Waiting for response — do NOT continue until you receive "
        "the user's answers in the next message."
    )


#: The event that closes a confirmation card. ``answer`` is one of
#: :data:`CONFIRMATION_ANSWERS`. The client drops the card whose
#: ``request_id`` it names, live and on a replay. Fence:
#: ``tests/unit/test_genui_hitl.py`` (the ``confirmation_resolved`` tests).
CONFIRMATION_RESOLVED = "confirmation_resolved"
CONFIRMATION_ANSWERS: frozenset[str] = frozenset(
    {"APPROVE", "REJECT", "TIMEOUT", "CANCELLED"}
)


def _resolved_event(request_id: str, answer: str) -> dict:
    return {
        "type": "CUSTOM",
        "name": CONFIRMATION_RESOLVED,
        "value": {"request_id": request_id, "answer": answer},
    }


async def request_confirmation(
    title: str, detail: str = "", context: str = "",
    non_interactive_default: str = "deny",
) -> bool:
    """Emit a HITL confirmation card and BLOCK until the user approves/rejects.

    Renders a ``ConfirmationCard`` inline in the chat with Approve / Reject
    buttons and parks the agent on a Future (same blocking mechanism as
    :func:`ask_questions`).  Use this to gate an outward-facing or irreversible
    action — e.g. before actually sending an email.

    Args:
        title: short card heading, e.g. ``"Send this email?"``.
        detail: one-line summary, e.g. ``"To a@b.com · Subject: Hi"``.
        context: longer preformatted body shown in a scrollable block
            (e.g. the email body the user is about to send).
        non_interactive_default: what happens when there is NO channel to
            deliver the card (a non-interactive/automated run).  The default
            ``"deny"`` FAILS CLOSED — destructive actions never auto-approve
            without a human (HH-2, OWASP LLM06 excessive agency).  Pass
            ``"approve"`` ONLY for reversible actions where automation must
            proceed unattended.

    Returns:
        ``True`` if the user approved, ``False`` if they rejected or did not
        respond.  When no delivery channel exists, returns ``True`` only if
        ``non_interactive_default="approve"`` was explicitly passed.
    """
    _title = str(title or "Confirm action").strip()[:120]
    _detail = str(detail or "").strip()[:500]
    _context = str(context or "").strip()[:4000]

    def _event(request_id: str) -> dict:
        return {
            "type": "CUSTOM",
            "name": "confirmation_requested",
            "value": {
                "title": _title,
                "detail": _detail,
                "context": _context,
                "request_id": request_id,
            },
        }

    async def _block_on(_fut, _rid, _pending, _publish) -> bool:
        """Park on the answer, then say on the stream that the card is closed.

        A model can call card-gated tools in parallel, so one stream can carry
        many ``confirmation_requested`` cards at once, and a replay sends all
        of them again. With no closing event, the client cannot tell an
        answered card from a waiting one. It showed the answered card again
        (2026-10-06). ``_publish`` sends ``confirmation_resolved`` on the
        stream that carried the card, on every way out: an answer, a timeout
        and a cancel.
        """
        import asyncio as _asyncio
        import contextlib as _contextlib
        _answer = "TIMEOUT"
        try:
            from orchestrator.executor import wait_user_future  # noqa: PLC0415
            _result = await wait_user_future(_fut, 3600)
            _answer = (
                "APPROVE"
                if str(_result.get("answer", "")).strip().upper() == "APPROVE"
                else "REJECT"
            )
        except _asyncio.CancelledError:
            _answer = "CANCELLED"
            raise
        except TimeoutError:
            pass
        finally:
            _pending.pop(_rid, None)
            # Best effort: the run's end clears a blocking card too.
            with _contextlib.suppress(Exception):
                await _publish(_resolved_event(_rid, _answer))
        return _answer == "APPROVE"

    # ── Path A: _active_run_queue (native MAF / Tier 2 blocking) ──────────
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_run_queue,
            _pending_user_input,
        )
        queue = _active_run_queue.get(None)
    except Exception:  # noqa: BLE001
        queue = None
    if queue is not None:
        import asyncio as _asyncio
        import uuid as _uuid

        _rid = _uuid.uuid4().hex
        _fut = _asyncio.get_running_loop().create_future()
        _pending_user_input[_rid] = _fut
        await queue.put(_event(_rid))
        return await _block_on(_fut, _rid, _pending_user_input, queue.put)

    # ── Path C: Redis relay (blocking on BOTH Copilot-SDK and native MAF) ──
    # resolve_relay_thread_id survives the Copilot SDK thread hop that resets the
    # _stream_relay_thread_id ContextVar. WITHOUT this, a Copilot agent's
    # request_confirmation found no thread_id, skipped this path, and fell through
    # to the non-interactive default below — silently DENYING the action without
    # ever showing the user the confirmation card. This is the safety gate for
    # send_email etc., so blocking must work on the Copilot path too.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _pending_user_input,
            _push_sse_to_stream,
            resolve_relay_thread_id,
        )
        _tid = resolve_relay_thread_id()
        if _tid:
            import asyncio as _asyncio
            import uuid as _uuid

            _rid = _uuid.uuid4().hex
            _fut = _asyncio.get_running_loop().create_future()
            _pending_user_input[_rid] = _fut
            _line = f"data: {_json.dumps(_event(_rid))}\n\n"
            await _push_sse_to_stream(_tid, _line)

            async def _publish(ev: dict, _t: str = _tid) -> None:
                await _push_sse_to_stream(_t, f"data: {_json.dumps(ev)}\n\n")

            return await _block_on(_fut, _rid, _pending_user_input, _publish)
    except Exception:  # noqa: BLE001
        pass

    # No delivery channel (non-interactive run) — fail CLOSED unless the
    # caller explicitly opted a reversible action into auto-approval.
    return str(non_interactive_default).strip().lower() == "approve"
