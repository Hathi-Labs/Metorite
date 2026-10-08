/**
 * askPin — the one element that waits on the member, for the pin above the
 * composer.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24, rule 1 (owner,
 * 2026-10-08). An element that NEEDS THE MEMBER stays in the flow, in order.
 * While it waits, a compact bar above the composer names it, so it can never
 * scroll out of view under a long trail or a later card. The bar shows only
 * while the element itself is out of view (`components/AskPin.tsx`), and a
 * press scrolls to it.
 *
 * This file decides WHAT waits. It reuses the state that already exists and
 * adds none of its own:
 *
 * - the confirmation queue (`lib/confirmationQueue.ts`), with its "1 of N";
 * - the `ask_questions` card and the native `ask_user` prompt (`AgentChat`);
 * - a generative-UI ask in the newest turn (`lib/chatPlacement.ts`
 *   `genUiPlacement`).
 *
 * When the element goes, the pin goes:
 *
 * - an answered confirmation or question leaves its own state;
 * - a BLOCKING generative-UI ask (it has a `request_id`) waits only while the
 *   run is live and the member has not answered it;
 * - a non-blocking ask is answered by a new chat message, so it waits only
 *   while its turn is the newest message.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/askPin.test.ts`.
 */

import { genUiPlacement, genUiTitle } from "@/lib/chatPlacement";
import type { PendingConfirmation } from "@/lib/confirmationQueue";

/** What the pin draws. `target` names the element it scrolls to. */
export interface PendingAsk {
  kind: "confirm" | "question" | "choice";
  title: string;
  /** How many of this kind wait. More than 1 only for the confirmation queue. */
  count: number;
  /** The `data-chat-ask` value of the element in the thread. */
  target: string;
}

/** The `data-chat-ask` value of the inline HITL group (`AgentChat`). */
export const HITL_TARGET = "hitl";

/** The `data-chat-ask` value of one generative-UI spec (`MessageBubble`). */
export function genUiTarget(messageId: string, index: number, spec: unknown): string {
  const rid = spec && typeof spec === "object" ? (spec as Record<string, unknown>).request_id : undefined;
  return typeof rid === "string" && rid ? `genui:${rid}` : `genui:${messageId}:${index}`;
}

export interface AskSources {
  confirmations: readonly PendingConfirmation[];
  elicitation: { questions: readonly { question?: string; header?: string }[] } | null;
  userInput: { question: string } | null;
  messages: readonly { id: string; role: string; customEvents?: { name: string; value: unknown }[] }[];
  /** The run is live: a blocking ask can still be answered. */
  runActive: boolean;
  /** The `request_id`s of generative-UI asks the member answered. */
  answered: ReadonlySet<string>;
}

/** The longest title the bar draws. The bar is one line. */
export const PIN_TITLE_CAP = 120;

function cap(text: string): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length > PIN_TITLE_CAP ? `${one.slice(0, PIN_TITLE_CAP - 1)}…` : one;
}

/** The generative-UI ask of the newest turn, or null. */
function genUiAsk(src: AskSources): PendingAsk | null {
  const last = src.messages[src.messages.length - 1];
  // A non-blocking ask is answered by a chat message: once a later message
  // exists, it waits on nobody. A blocking one streams into this turn.
  if (!last || last.role !== "assistant") return null;
  const specs = (last.customEvents ?? [])
    .filter((e) => e.name === "generative_ui" && e.value != null)
    .map((e) => e.value);
  for (let i = specs.length - 1; i >= 0; i--) {
    const spec = specs[i];
    if (genUiPlacement(spec) !== "ask") continue;
    const rec = spec as Record<string, unknown>;
    if (rec.surface === "panel") continue; // the side panel holds it, not the thread
    const rid = typeof rec.request_id === "string" ? rec.request_id : "";
    if (rid && (src.answered.has(rid) || !src.runActive)) continue;
    return {
      kind: "choice",
      title: cap(genUiTitle(spec) || "A choice waits for you"),
      count: 1,
      target: genUiTarget(last.id, i, spec),
    };
  }
  return null;
}

/**
 * The element the pin names, or null when nothing waits. A confirmation card
 * comes first: it holds a write, and the run is parked on it.
 */
export function pendingAsk(src: AskSources): PendingAsk | null {
  if (src.confirmations.length > 0) {
    return {
      kind: "confirm",
      title: cap(src.confirmations[0].title || "Confirm an action"),
      count: src.confirmations.length,
      target: HITL_TARGET,
    };
  }
  if (src.elicitation && src.elicitation.questions.length > 0) {
    const q = src.elicitation.questions[0];
    return {
      kind: "question",
      title: cap(q.question || q.header || "A question waits for you"),
      count: src.elicitation.questions.length,
      target: HITL_TARGET,
    };
  }
  if (src.userInput) {
    return { kind: "question", title: cap(src.userInput.question), count: 1, target: HITL_TARGET };
  }
  return genUiAsk(src);
}

/** The bar's lead word for each kind. */
export const PIN_LEAD: Record<PendingAsk["kind"], string> = {
  confirm: "Waiting for your approval",
  question: "Waiting for your answer",
  choice: "Waiting for your choice",
};
