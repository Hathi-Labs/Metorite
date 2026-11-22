/**
 * runStep — the step of a run, in words, for the one run state
 * (`lib/runStatus.ts`, `chat_run_continuity.md` §4.10). Its own module, so
 * the chat store (which imports `runStatus.ts`) does not load the step
 * vocabulary. Fence (R7): `runStatus.test.ts`.
 */

import type { ChatMessage } from "@/lib/chatStore";
import { describeToolStep } from "@/lib/toolSteps";

// ── The step, in words ─────────────────────────────────────────────────────

/** The step words while the reply's text streams. */
export const WRITING_STEP = "Writing the reply";
/** The step words while the model reasons. */
export const THINKING_STEP = "Thinking";

/**
 * CS-3b (`chat_failure_modes.md` §6 row CS-3): the fixed words for the server's phase of a run.
 * ONE table. The server sends only the phase name, never words, so the client draws only a word
 * from here. A name that is not a key is ignored.
 */
export const RUN_PHASE_WORDS = {
  gathering: "Gathering what I need",
  asking: "Asking the model",
} as const;

export type RunPhaseName = keyof typeof RUN_PHASE_WORDS;

/** The words of a `run_phase` event value, or null when the value is not a known phase. */
export function phaseWordOf(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const name = (value as { phase?: unknown }).phase;
  if (typeof name !== "string" || !Object.prototype.hasOwnProperty.call(RUN_PHASE_WORDS, name)) return null;
  return RUN_PHASE_WORDS[name as RunPhaseName];
}

/**
 * The step of a message, in words, from the one step vocabulary
 * (`lib/toolSteps.ts`): the running tool ("Asking Projects assistant"),
 * else the text ("Writing the reply"), else the reasoning ("Thinking").
 */
export function stepOfMessage(m: ChatMessage | undefined): string | null {
  if (!m || m.role !== "assistant") return null;
  const tools = m.toolEvents ?? [];
  for (let i = tools.length - 1; i >= 0; i--) {
    const t = tools[i];
    if (t.status !== "running") continue;
    // A hand-off reads "Asking Projects assistant": the label names the agent.
    return describeToolStep(t).label;
  }
  if (m.content.trim()) return WRITING_STEP;
  if ((m.reasoningBlocks?.length ?? 0) > 0) return THINKING_STEP;
  return null;
}
