/**
 * The steps of a delegated run, as tool events a card file can draw.
 *
 * D99 (`navigation_shell.md` §7.1.1 rule 5). The member talks to the one
 * assistant, and email work runs in `email-assistant` by delegation. The
 * sub-run's tool calls arrive as `subAgentTools` on the hand-off's row, and
 * the email cards read top-level tool events only. So the draft card, the
 * thread card and the rule cards did not draw in the one assistant.
 *
 * These helpers turn each step of the named agent into a `ToolEvent`, with
 * its own arguments and result. The cards then draw with the placement rules
 * they already have (`projects_ai_chat.md` §24 and §24.10). The account of a
 * card comes from the step's own `account_id`, never from a page context.
 *
 * Pure. Fence (R7): `delegatedSteps.test.ts`.
 */

import { isSpecialistTool } from "@/lib/chatStream";

type Status = "running" | "done" | "error";

/** One step of a sub-run, as `chatStore.SubAgentTool` and the card type
 *  (`MarkdownMessage.ToolEvent`) each hold it. */
export interface StepLike {
  id: string;
  name: string;
  status: Status;
  result?: string;
  args?: Record<string, unknown>;
}

/** A tool event of the turn, in either of the two shapes the UI uses. */
export interface HandOffLike {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: Status;
  subAgentName?: string;
  subAgentTools?: StepLike[];
}

/** What a card file reads from one tool event. */
export interface StepEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: Status;
}

/** The agent whose delegated steps carry email cards. */
export const EMAIL_AGENT = "email-assistant";

/** One delegated step as a tool event. The id is the step's own id. */
export function stepAsToolEvent(step: StepLike): StepEvent {
  return {
    id: step.id,
    name: step.name,
    args: step.args ?? {},
    result: step.result,
    status: step.status,
  };
}

/** Is this row a hand-off to `agent`, by its sub-agent name or its tool? */
function handsOffTo(e: HandOffLike, agent: string): boolean {
  return e.subAgentName === agent || isSpecialistTool(e.name, agent);
}

/** Each step of `agent`'s delegated runs in `events`, in order. */
export function delegatedSteps(events: readonly HandOffLike[] | undefined, agent: string): StepEvent[] {
  const out: StepEvent[] = [];
  const seen = new Set((events ?? []).map((e) => e.id));
  for (const e of events ?? []) {
    if (!handsOffTo(e, agent)) continue;
    for (const st of e.subAgentTools ?? []) {
      if (!st.id || seen.has(st.id)) continue;
      seen.add(st.id);
      out.push(stepAsToolEvent(st));
    }
  }
  return out;
}

/** `events`, then each step of the email agent's delegated runs. */
export function withEmailSteps<E extends HandOffLike>(events: readonly E[] | undefined): Array<E | StepEvent> {
  return [...(events ?? []), ...delegatedSteps(events, EMAIL_AGENT)];
}
