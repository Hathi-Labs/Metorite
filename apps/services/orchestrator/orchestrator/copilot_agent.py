from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterable
from typing import Any

from acb_llm import compress_tool_output
from agent_framework import AgentResponseUpdate, Content
from agent_framework.exceptions import AgentException
from agent_framework_github_copilot import GitHubCopilotAgent
from copilot import CopilotClient, CopilotSession, SessionEvent
from copilot.session import SessionEventType

from orchestrator._copilot_session import (
    effective_infinite_sessions as _effective_infinite_sessions,
)

logger = logging.getLogger(__name__)

# ── Output token limit (CURRENTLY A NO-OP — see below) ───────────────────
# WARNING: this value does NOT reach the model. The Copilot SDK's
# create_session / resume_session wire protocol has no max_tokens field, so the
# ``config["max_tokens"]`` we set below is silently dropped by the SDK. The
# real output ceiling is applied by the gateway's /v1 endpoint
# (GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS in v1_compat.py), which is the choke point
# every Copilot request actually flows through — the CLI POSTs there WITHOUT a
# max_tokens, so the gateway's default is what binds.
#
# Kept (with the config lines below) so the cap starts working automatically if
# a future SDK version adds the field — but do NOT tune this expecting an
# effect: raise GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS instead.
_MAX_OUTPUT_TOKENS: int = int(
    os.environ.get("COPILOT_MAX_OUTPUT_TOKENS", "16000")
)

# ── Stream stall detection ────────────────────────────────────────────────
# If no Copilot SDK event arrives for this many seconds, the CLI subprocess
# is likely dead/hung.  Raise an exception so the executor can retry or fail
# gracefully instead of leaving the HTTP stream hanging forever.
_STREAM_STALL_TIMEOUT: float = float(
    os.environ.get("COPILOT_STREAM_STALL_TIMEOUT", "300")
)

# While a blocking HITL question (ask_user / ask_questions) is parked waiting
# for the human, the session legitimately emits nothing — that silence is NOT
# a stall. Give the human this much time to answer (same knob the native-MAF
# tiered watchdog uses) before the stall detector may fire.
_HITL_STALL_TIMEOUT: float = float(
    os.environ.get("HITL_IDLE_TIMEOUT_SECONDS", "3600")
)

# While a tool is executing, the session can be legitimately quiet for far
# longer than the bare stall budget — a multi-minute shell build/test run
# buffers its output and emits no TOOL_EXECUTION_PROGRESS for the duration.
# Mirror the native-MAF watchdog's tool_open tier (same env knob,
# WatchdogPolicy.tool_open) so a working tool isn't killed at 300s with a
# misleading "CLI subprocess may have crashed" (audit C3).
_TOOL_STALL_TIMEOUT: float = float(
    os.environ.get("NATIVE_TOOL_IDLE_TIMEOUT_SECONDS", "600")
)


def _hitl_pending() -> bool:
    """True while any blocking ask_user / ask_questions Future awaits the user.

    Reads the executor's pending-input registry. Global across runs — in the
    rare case another thread's question is pending, this errs on the side of
    not killing a possibly-waiting run.
    """
    try:
        from orchestrator.executor import _pending_user_input
        return bool(_pending_user_input)
    except Exception:
        return False


class MetoriteCopilotAgent(GitHubCopilotAgent):
    """GitHubCopilotAgent with BYOK provider forwarding and rich event streaming.

    Extends the standard GitHubCopilotAgent to:
    1. Forward ``provider`` config for BYOK (Bring Your Own Key) support
    2. Emit additional event types: reasoning/thinking, tool progress,
       partial terminal output, agent intent/status
    3. Store ``raw_representation`` on every AgentResponseUpdate for
       downstream AG-UI event translation
    4. Headless auth: pass COPILOT_GITHUB_TOKEN explicitly to the Copilot
       CLI (servers have no logged-in ``copilot`` CLI user)
    """

    async def start(self) -> None:
        """Start the Copilot client with explicit token auth when available.

        The upstream GitHubCopilotAgent.start() relies on the CLI's
        logged-in user, which does not exist on headless servers.  When
        COPILOT_GITHUB_TOKEN (preferred) or GITHUB_COPILOT_TOKEN is set,
        construct the client with ``github_token`` so the SDK forwards it
        as COPILOT_SDK_AUTH_TOKEN to the CLI subprocess.

        BO-7 phase 2: when ``self._sandbox_cli_url`` is set (by
        ``code_session.py`` / ``executor.py`` after spawning a
        ``copilot_sandbox`` container), connect to that external CLI server
        over TCP instead — the SDK's ``cli_url`` transport — rather than
        spawning a local subprocess. ``github_token``/``cli_url`` are mutually
        exclusive per the SDK's own validation, so this branch and the token
        branch below never both fire.
        """
        sandbox_cli_url = getattr(self, "_sandbox_cli_url", None)
        if self._client is None and sandbox_cli_url:
            self._client = CopilotClient({"cli_url": sandbox_cli_url})
            logger.info("Copilot client connected to sandbox", cli_url=sandbox_cli_url)
            await GitHubCopilotAgent.start(self)
            return

        token = (
            os.environ.get("COPILOT_GITHUB_TOKEN")
            or os.environ.get("GITHUB_COPILOT_TOKEN")
            # GITHUB_TOKEN is the canonical secret the connect UI / settings
            # store / DB hydration populate; COPILOT_GITHUB_TOKEN is rarely set
            # in practice, so fall back to it to match the models + mutation
            # paths (else headless runs hit "Authorization error, run /login").
            or os.environ.get("GITHUB_TOKEN")
            or ""
        ).strip()
        if self._client is None and token:
            client_options: dict[str, Any] = {"github_token": token}
            cli_path = self._settings.get("cli_path")
            if cli_path:
                client_options["cli_path"] = cli_path
            log_level = self._settings.get("log_level")
            if log_level:
                client_options["log_level"] = log_level
            self._client = CopilotClient(client_options)
            logger.info("Copilot client using explicit token auth")
        # Explicit base call (not super()): this method is monkey-patched
        # onto plain GitHubCopilotAgent instances, where zero-arg super()
        # raises "obj must be an instance or subtype of type".
        await GitHubCopilotAgent.start(self)

    async def _create_session(
        self,
        streaming: bool,
        runtime_options: dict[str, Any] | None = None,
    ) -> CopilotSession:
        """Create Copilot session with BYOK provider support."""
        if not self._client:
            raise RuntimeError("GitHub Copilot client not initialized. Call start() first.")

        opts = runtime_options or {}
        config: dict[str, Any] = {"streaming": streaming}

        model = opts.get("model") or self._default_options.get("model") or self._settings.get("model")
        if model:
            config["model"] = model

        system_message = opts.get("system_message") or self._default_options.get("system_message")
        if system_message:
            config["system_message"] = system_message

        if self._tools:
            config["tools"] = self._prepare_tools(self._tools)

        permission_handler = opts.get("on_permission_request") or self._permission_handler
        if permission_handler:
            config["on_permission_request"] = permission_handler

        mcp_servers = opts.get("mcp_servers") or self._mcp_servers
        if mcp_servers:
            config["mcp_servers"] = mcp_servers

        # === Workspace: forward working_directory so the CLI's file ops,
        # shell commands, AGENTS.md and skill resolution all happen inside
        # the agent's own clone dir instead of the gateway process cwd.
        working_directory = (
            opts.get("working_directory")
            or self._default_options.get("working_directory")
        )
        if working_directory:
            config["working_directory"] = working_directory

        # === HITL: forward native ask_user handler so the run blocks on
        # user input instead of fire-and-forget messaging.
        user_input_handler = (
            opts.get("on_user_input_request")
            or self._default_options.get("on_user_input_request")
        )
        if user_input_handler:
            config["on_user_input_request"] = user_input_handler

        # === BYOK: forward provider config to Copilot SDK ===
        provider = opts.get("provider") or self._default_options.get("provider")
        if provider:
            config["provider"] = provider

        # === Output tokens: prevent model truncation mid-response ===
        # When unset, BYOK models use their provider's default output limit
        # (often 4096 tokens).  The agent's reasoning + tool calls + text can
        # easily exceed this, causing the model to stop mid-sentence.  Set a
        # generous ceiling so the agent can finish its thought.
        max_tokens = (
            opts.get("max_tokens")
            or self._default_options.get("max_tokens")
            or _MAX_OUTPUT_TOKENS
        )
        if max_tokens:
            config["max_tokens"] = int(max_tokens)

        # === Context compaction: neutralise the backend's window guessing ===
        # MUST be applied here, not only via the _apply_copilot_infinite_sessions
        # wrap: the executor rebinds ``agent._create_session`` to THIS method
        # after tool injection installs that wrap, so on the streaming chat path
        # the wrap is discarded before it ever runs. Applying the shared policy
        # directly makes the behaviour independent of binding order.
        _inf = _effective_infinite_sessions(self._default_options)
        if _inf is not None:
            config["infinite_sessions"] = _inf

        # === Reasoning: forward effort so thinking deltas stream ===
        effort = (
            opts.get("reasoning_effort")
            or self._default_options.get("reasoning_effort")
        )
        if effort:
            config["reasoning_effort"] = effort
            try:
                return await self._client.create_session(config)
            except Exception:
                # Model may not support reasoning_effort — retry without.
                logger.warning(
                    "create_session failed with reasoning_effort=%s; "
                    "retrying without", effort,
                )
                config.pop("reasoning_effort", None)

        return await self._client.create_session(config)

    async def _resume_session(
        self,
        session_id: str,
        streaming: bool,
    ) -> CopilotSession:
        """Resume a Copilot session, re-applying the agent's identity.

        The upstream ``_resume_session`` forwards only ``tools``,
        ``on_permission_request`` and ``mcp_servers`` — it drops
        ``system_message`` (the agent's instructions / identity),
        ``provider`` (BYOK routing) and ``model``.  On page refresh or
        when re-opening an old chat the stored session is resumed, so
        without these the agent loses its persona and reverts to the
        generic GitHub Copilot CLI identity.  Re-apply them here so a
        resumed session behaves identically to a freshly created one.
        """
        if not self._client:
            raise RuntimeError(
                "GitHub Copilot client not initialized. Call start() first."
            )

        config: dict[str, Any] = {"streaming": streaming}

        model = self._default_options.get("model") or self._settings.get("model")
        if model:
            config["model"] = model

        system_message = self._default_options.get("system_message")
        if system_message:
            config["system_message"] = system_message

        if self._tools:
            config["tools"] = self._prepare_tools(self._tools)

        if self._permission_handler:
            config["on_permission_request"] = self._permission_handler

        mcp_servers = self._mcp_servers
        if mcp_servers:
            config["mcp_servers"] = mcp_servers

        # === Workspace: re-apply working_directory on resume so an old chat
        # reopened after refresh still operates in the agent's clone dir.
        working_directory = self._default_options.get("working_directory")
        if working_directory:
            config["working_directory"] = working_directory

        # === HITL: re-apply native ask_user handler on resume ===
        user_input_handler = self._default_options.get("on_user_input_request")
        if user_input_handler:
            config["on_user_input_request"] = user_input_handler

        # === BYOK: forward provider config so resumed sessions route correctly
        provider = self._default_options.get("provider")
        if provider:
            config["provider"] = provider

        # === Output tokens: re-apply on resume to avoid mid-thought cutoff ===
        max_tokens = self._default_options.get("max_tokens") or _MAX_OUTPUT_TOKENS
        if max_tokens:
            config["max_tokens"] = int(max_tokens)

        # === Context compaction: re-apply on resume ===
        # A resumed session rebuilds its config from scratch, so without this a
        # long-running BYOK thread would silently regain the backend's default
        # 0.80/0.95 compaction against its wrong ~90K window guess.
        _inf = _effective_infinite_sessions(self._default_options)
        if _inf is not None:
            config["infinite_sessions"] = _inf

        # === Reasoning: forward effort so thinking deltas keep streaming ===
        effort = self._default_options.get("reasoning_effort")
        if effort:
            config["reasoning_effort"] = effort
            try:
                return await self._client.resume_session(session_id, config)
            except Exception:
                logger.warning(
                    "resume_session failed with reasoning_effort=%s; "
                    "retrying without", effort,
                )
                config.pop("reasoning_effort", None)

        return await self._client.resume_session(session_id, config)

    async def _stream_updates(
        self,
        messages=None,
        *,
        session=None,
        options=None,
        _ctx_holder=None,
    ) -> AsyncIterable[AgentResponseUpdate]:
        """Stream updates with full event type coverage.

        Handles ALL Copilot SDK event types:
        - Text deltas (ASSISTANT_MESSAGE_DELTA)
        - Reasoning/thinking (ASSISTANT_REASONING_DELTA, ASSISTANT_REASONING)
        - Tool execution (TOOL_EXECUTION_START, TOOL_EXECUTION_COMPLETE,
          TOOL_EXECUTION_PROGRESS, TOOL_EXECUTION_PARTIAL_RESULT)
        - Agent intent/status (ASSISTANT_INTENT)
        - Final messages (ASSISTANT_MESSAGE)
        - Session lifecycle (SESSION_IDLE, SESSION_ERROR)
        """
        if not self._started:
            await self.start()

        if not session:
            session = self.create_session()

        opts: dict[str, Any] = dict(options) if options else {}

        from agent_framework import normalize_messages

        input_messages = normalize_messages(messages)

        session_context = await self._run_before_providers(
            session=session, input_messages=input_messages, options=opts
        )

        copilot_session = await self._get_or_create_session(
            session, streaming=True, runtime_options=opts
        )

        if _ctx_holder is not None:
            _ctx_holder["session_context"] = session_context
            _ctx_holder["session"] = session

        context_messages = session_context.get_messages(include_input=True)
        prompt = "\n".join([m.text for m in context_messages])
        if session_context.instructions:
            prompt = "\n".join(session_context.instructions) + "\n" + prompt

        queue: asyncio.Queue[AgentResponseUpdate | Exception | None] = asyncio.Queue()

        # Reasoning blocks already streamed as deltas — used to skip the
        # duplicate full-content ASSISTANT_REASONING event at block end.
        _streamed_reasoning_ids: set[str] = set()
        # Message ids whose text already streamed as deltas — the final
        # ASSISTANT_MESSAGE dedup is PER MESSAGE (a turn carries several
        # assistant messages: narration → tool → final answer), mirroring
        # _streamed_reasoning_ids for reasoning blocks.
        _streamed_message_ids: set[str] = set()

        # Accumulated assistant text from deltas — used to detect and skip
        # the duplicate full-content ASSISTANT_MESSAGE event at turn end.
        _accumulated_text: str = ""

        # Tool calls currently executing (started, not yet completed) — the
        # stall detector grants a longer quiet budget while any is in flight
        # (audit C3). A set of call ids so an unmatched COMPLETE can't skew it.
        _tools_in_flight: set[str] = set()

        def _on_event(event: SessionEvent) -> None:
            """Translate Copilot SDK events to AgentResponseUpdate objects."""
            nonlocal _accumulated_text
            try:
                t = event.type
                d = event.data

                # ── Debug: log every event type and content-bearing data ──
                _dbg_extra: dict[str, Any] = {}
                _has_delta = getattr(d, "delta_content", None)
                _has_content = getattr(d, "content", None)
                if _has_delta:
                    _dbg_extra["delta_len"] = len(str(_has_delta))
                if _has_content:
                    _dbg_extra["content_len"] = len(str(_has_content))
                _dbg_extra["accum_len"] = len(_accumulated_text)
                logger.debug(
                    "copilot_event: %s", t.value,
                    extra=_dbg_extra,
                )

                if t == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                    if d.delta_content:
                        _accumulated_text += (d.delta_content or "")
                        if getattr(d, "message_id", None):
                            _streamed_message_ids.add(str(d.message_id))
                        queue.put_nowait(AgentResponseUpdate(
                            role="assistant",
                            contents=[Content.from_text(d.delta_content)],
                            response_id=d.message_id,
                            message_id=d.message_id,
                            raw_representation=event,
                        ))

                elif t == SessionEventType.ASSISTANT_REASONING_DELTA:
                    delta = getattr(d, "delta_content", "") or ""
                    if delta:
                        _rid = (
                            getattr(d, "reasoning_id", None)
                            or getattr(d, "id", None)
                            or "_default"
                        )
                        _streamed_reasoning_ids.add(str(_rid))
                        queue.put_nowait(AgentResponseUpdate(
                            role="assistant",
                            contents=[Content.from_text_reasoning(text=delta)],
                            raw_representation=event,
                        ))

                elif t == SessionEventType.ASSISTANT_REASONING:
                    content = getattr(d, "content", "") or ""
                    _rid = (
                        getattr(d, "reasoning_id", None)
                        or getattr(d, "id", None)
                        or "_default"
                    )
                    # Skip if this block already streamed as deltas —
                    # emitting it again would duplicate the thinking text.
                    if content and str(_rid) not in _streamed_reasoning_ids:
                        queue.put_nowait(AgentResponseUpdate(
                            role="assistant",
                            contents=[Content.from_text_reasoning(text=content)],
                            raw_representation=event,
                        ))

                elif t == SessionEventType.ASSISTANT_INTENT:
                    intent = getattr(d, "intent", "") or ""
                    if intent:
                        # No text content — the executor translates the raw
                        # INTENT event into a timeline entry.  Emitting text
                        # here would pollute the visible assistant message.
                        queue.put_nowait(AgentResponseUpdate(
                            role="assistant",
                            contents=[],
                            raw_representation=event,
                        ))

                elif t == SessionEventType.TOOL_EXECUTION_START:
                    tc_id = getattr(d, "tool_call_id", "") or ""
                    tc_name = getattr(d, "tool_name", "") or ""
                    args = getattr(d, "arguments", None)
                    _tools_in_flight.add(tc_id or tc_name or "tool")
                    queue.put_nowait(AgentResponseUpdate(
                        role="assistant",
                        contents=[Content.from_function_call(
                            call_id=tc_id,
                            name=tc_name,
                            arguments=args,
                            raw_representation=d,
                        )],
                        raw_representation=event,
                    ))

                elif t == SessionEventType.TOOL_EXECUTION_COMPLETE:
                    tc_id = getattr(d, "tool_call_id", "") or ""
                    tc_name = getattr(d, "tool_name", "") or ""
                    _tools_in_flight.discard(tc_id or tc_name or "tool")
                    result_obj = getattr(d, "result", None)
                    result_text = getattr(result_obj, "content", "") if result_obj else ""
                    success = getattr(d, "success", None)
                    error_val = getattr(d, "error", None)
                    exception = None
                    if success is False and error_val is not None:
                        exception = (
                            error_val.message
                            if hasattr(error_val, "message")
                            else str(error_val)
                        )
                    # RTK-style: compress large shell/test/build output before it
                    # re-enters context (gated on tool name; structured tool
                    # results and small output pass through untouched). Full
                    # output stays in the run trace. See Item ② of
                    # specs/runtime_agent_effectiveness_2026-07.md.
                    queue.put_nowait(AgentResponseUpdate(
                        role="tool",
                        contents=[Content.from_function_result(
                            call_id=tc_id,
                            result=compress_tool_output(tc_name, result_text or ""),
                            exception=exception,
                            raw_representation=d,
                        )],
                        raw_representation=event,
                    ))

                # ── External / custom tool execution (our injected tools) ──
                # Custom tools registered via _register_tools() use the
                # EXTERNAL_TOOL_REQUESTED / EXTERNAL_TOOL_COMPLETED event
                # pair instead of the built-in TOOL_EXECUTION_* events.
                # Without these handlers, our injected tools (manage_todo_list,
                # ask_questions, etc.) execute but are invisible to the
                # streaming loop — no TODO_LIST events, no UI updates.
                elif t == SessionEventType.EXTERNAL_TOOL_REQUESTED:
                    tc_id = getattr(d, "tool_call_id", "") or ""
                    tc_name = getattr(d, "tool_name", "") or ""
                    args = getattr(d, "arguments", None)
                    _tools_in_flight.add(tc_id or tc_name or "tool")
                    queue.put_nowait(AgentResponseUpdate(
                        role="assistant",
                        contents=[Content.from_function_call(
                            call_id=tc_id,
                            name=tc_name,
                            arguments=args,
                            raw_representation=d,
                        )],
                        raw_representation=event,
                    ))

                elif t == SessionEventType.EXTERNAL_TOOL_COMPLETED:
                    tc_id = getattr(d, "tool_call_id", "") or ""
                    tc_name = getattr(d, "tool_name", "") or ""
                    _tools_in_flight.discard(tc_id or tc_name or "tool")
                    result_obj = getattr(d, "result", None)
                    result_text = (
                        getattr(result_obj, "content", "")
                        if result_obj else ""
                    )
                    success = getattr(d, "success", None)
                    error_val = getattr(d, "error", None)
                    exception = None
                    if success is False and error_val is not None:
                        exception = (
                            error_val.message
                            if hasattr(error_val, "message")
                            else str(error_val)
                        )
                    # Same RTK-style compression; the tool-name gate means our
                    # structured custom tools (manage_todo_list, ask_questions, …)
                    # pass through UNTOUCHED — only a custom tool that shells out
                    # would ever be compressed. See Item ② of the spec.
                    queue.put_nowait(AgentResponseUpdate(
                        role="tool",
                        contents=[Content.from_function_result(
                            call_id=tc_id,
                            result=compress_tool_output(tc_name, result_text or ""),
                            exception=exception,
                            raw_representation=d,
                        )],
                        raw_representation=event,
                    ))

                elif t == SessionEventType.TOOL_EXECUTION_PROGRESS:
                    progress = (
                        getattr(d, "progress_message", None)
                        or getattr(d, "progress", None)
                        or getattr(d, "message", None)
                        or ""
                    )
                    if progress:
                        queue.put_nowait(AgentResponseUpdate(
                            role="tool",
                            contents=[Content.from_text(f"[progress] {progress}")],
                            raw_representation=event,
                        ))

                elif t == SessionEventType.TOOL_EXECUTION_PARTIAL_RESULT:
                    partial = (
                        getattr(d, "partial_output", None)
                        or getattr(d, "partialOutput", None)
                        or ""
                    )
                    if partial:
                        queue.put_nowait(AgentResponseUpdate(
                            role="tool",
                            contents=[Content.from_text(partial)],
                            raw_representation=event,
                        ))

                elif t == SessionEventType.ASSISTANT_MESSAGE:
                    content = getattr(d, "content", "") or ""
                    _mid = str(getattr(d, "message_id", "") or "")
                    # ASSISTANT_MESSAGE duplicates deltas ONLY for the message
                    # id that actually streamed — dedup must be PER MESSAGE,
                    # not per turn.  The old turn-global guard
                    # (`not _accumulated_text`) dropped any LATER full message
                    # whose deltas never fired (common for the post-tool final
                    # answer): the visible output then held only the pre-tool
                    # narration, and the real final text survived nowhere but
                    # the folded thinking timeline — "the answer got wrapped
                    # inside the consciousness stream".
                    if _mid:
                        _already_streamed = _mid in _streamed_message_ids
                    else:
                        # No message id — fall back to exact-duplicate
                        # detection against the turn's accumulated deltas.
                        _already_streamed = bool(
                            content and content in _accumulated_text
                        )
                    if content and not _already_streamed:
                        _accumulated_text += content
                        if _mid:
                            _streamed_message_ids.add(_mid)
                        queue.put_nowait(AgentResponseUpdate(
                            role="assistant",
                            contents=[Content.from_text(content)],
                            response_id=d.message_id,
                            message_id=d.message_id,
                            raw_representation=event,
                        ))

                elif t == SessionEventType.SESSION_IDLE:
                    queue.put_nowait(None)

                elif t == SessionEventType.SESSION_ERROR:
                    error_msg = getattr(d, "message", "") or "Unknown error"
                    queue.put_nowait(
                        AgentException(f"GitHub Copilot session error: {error_msg}")
                    )

            except Exception:
                logger.exception(
                    "Error in MetoriteCopilotAgent event handler"
                )

        copilot_session.on(_on_event)

        try:
            await copilot_session.send({"prompt": prompt})

            # ── Stream stall detection ───────────────────────────────
            # If the Copilot CLI subprocess crashes or hangs, queue.get()
            # blocks forever.  A timeout turns a silent hang into a
            # visible error that the executor can retry or surface.
            last_event_time = asyncio.get_running_loop().time()
            while True:
                try:
                    update = await asyncio.wait_for(
                        queue.get(),
                        timeout=_STREAM_STALL_TIMEOUT,
                    )
                    last_event_time = asyncio.get_running_loop().time()
                except TimeoutError:
                    elapsed = (
                        asyncio.get_running_loop().time()
                        - last_event_time
                    )
                    # ── HITL-aware suppression ────────────────────────
                    # A blocking ask_user / ask_questions parks the run on
                    # a Future while the human answers; the session emits
                    # no events during that wait. Without this check the
                    # stall detector killed the run after 5 minutes, the
                    # executor emitted RUN_FINISHED/RUN_ERROR, and the
                    # frontend cleared the question card — questions
                    # "disappeared" before the user could answer. Keep
                    # waiting up to the HITL budget instead.
                    if _hitl_pending() and elapsed < _HITL_STALL_TIMEOUT:
                        logger.info(
                            "copilot_stream_quiet_hitl_pending: waiting on "
                            "user input for %.0fs (budget=%.0fs) — not a stall",
                            elapsed, _HITL_STALL_TIMEOUT,
                        )
                        continue
                    # ── Tool-in-flight grace (audit C3) ───────────────
                    # A running tool (long shell build/test, big file op)
                    # legitimately emits nothing until it completes — give
                    # it the same extended budget the native-MAF watchdog
                    # grants an open tool instead of killing the run with
                    # a misleading "CLI crashed" at the bare stall limit.
                    if _tools_in_flight and elapsed < _TOOL_STALL_TIMEOUT:
                        logger.info(
                            "copilot_stream_quiet_tool_running: %d tool(s) "
                            "in flight for %.0fs (budget=%.0fs) — not a stall",
                            len(_tools_in_flight), elapsed, _TOOL_STALL_TIMEOUT,
                        )
                        continue
                    logger.error(
                        "copilot_stream_stalled: no event for %.0fs "
                        "(stall_timeout=%.0fs, tools_in_flight=%d)",
                        elapsed, _STREAM_STALL_TIMEOUT, len(_tools_in_flight),
                    )
                    raise AgentException(
                        f"Copilot session stalled: no event for "
                        f"{elapsed:.0f}s (timeout={_STREAM_STALL_TIMEOUT}s). "
                        f"The CLI subprocess may have crashed."
                    ) from None
                if update is None:
                    break
                if isinstance(update, Exception):
                    raise update
                yield update
        except (asyncio.CancelledError, GeneratorExit):
            # Stop pressed (cancel_run cancelled our detached task) or a new run
            # superseded this one.  Detaching the event handler alone leaves the
            # Copilot CLI subprocess still generating in the background — a zombie
            # that burns tokens and keeps writing files.  Abort the in-flight
            # message so the CLI actually stops; the session itself stays valid
            # for reuse/resume.  Shield so our own cancellation doesn't interrupt
            # the abort RPC before the CLI receives it.
            try:
                await asyncio.shield(copilot_session.abort())
            except BaseException:
                pass
            raise
        finally:
            with contextlib.suppress(Exception):
                copilot_session.off(_on_event)

    async def _run_impl(
        self,
        messages=None,
        *,
        session=None,
        options=None,
    ):
        """Non-streaming run that inherits BYOK forwarding from _create_session."""
        return await super()._run_impl(
            messages=messages, session=session, options=options
        )
