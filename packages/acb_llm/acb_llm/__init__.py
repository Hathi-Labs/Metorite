"""LiteLLM-backed LLM client with tiered routing (ADR-005, ADR-008).

All app code MUST go through this module — never call provider SDKs directly.
"""
from acb_llm.client import LLMTier, complete, complete_with_tools
from acb_llm.context import (
    acompletion_with_fallback,
    assemble_run_context,
    context_window_for,
    count_message_tokens,
    fit_messages_to_context,
    resolve_underlying_model,
)
from acb_llm.decide import (
    BooleanAnswer,
    BooleanQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecideError,
    DecideRequestInvalid,
    DecideUnavailable,
    Decision,
    ScoreAnswer,
    ScoreQuestion,
    decide,
)
from acb_llm.message_compress import compress_message_content
from acb_llm.tool_output import compress_tool_output, is_compressible_tool

__all__ = [
    "BooleanAnswer",
    "BooleanQuestion",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "DecideError",
    "DecideRequestInvalid",
    "DecideUnavailable",
    "Decision",
    "LLMTier",
    "ScoreAnswer",
    "ScoreQuestion",
    "acompletion_with_fallback",
    "assemble_run_context",
    "complete",
    "complete_with_tools",
    "compress_message_content",
    "compress_tool_output",
    "context_window_for",
    "count_message_tokens",
    "decide",
    "fit_messages_to_context",
    "is_compressible_tool",
    "resolve_underlying_model",
]
