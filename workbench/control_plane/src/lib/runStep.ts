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
