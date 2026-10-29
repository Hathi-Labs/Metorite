/**
 * How a task card looks while an AI agent holds it (WS-55 EA-13g,
 * `external_agent_access.md` §12.2 item 7).
 *
 * One pure decision: a run state in, an edge and a line out. The board card
 * only applies the edge as `data-agent-edge` and draws the line. The colours
 * and the motion live in `globals.css` (`[data-agent-edge]`), from tokens.
 * The state names come from `AGENT_STATES` in `lib/statusAccent.ts`.
 *
 * | state            | edge          | line                              |
 * | working/answered | live          | "<client> · <agent_note>"         |
 * | queued           | dashed-accent | "Queued for <client>"             |
 * | awaiting         | warning       | "Needs you: <question>"           |
 * | stale            | muted         | "<client> idle"                   |
 * | failed           | destructive   | none (the Failed chip says it)    |
 * | no open run      | none          | none                              |
 *
 * Only "live" moves, because motion means "working".
 */

import { type AgentPillFacts, agentPillText, asAgentState, clientName } from "./agentRuns";

export type AgentEdge = "live" | "dashed-accent" | "warning" | "muted" | "destructive";

export interface AgentCardLook {
  edge: AgentEdge | null;
  line: string | null;
}

export const NO_AGENT_LOOK: AgentCardLook = { edge: null, line: null };

export function agentCardLook(
  state: string | null | undefined,
  facts: AgentPillFacts = {},
): AgentCardLook {
  const known = asAgentState(state);
  if (!known) return NO_AGENT_LOOK;
  const client = clientName(facts.clientVia);
  const note = facts.note?.trim() || null;
  switch (known) {
    case "working":
    case "answered":
      return {
        edge: "live",
        line: note
          ? `${client} · ${note}`
          : known === "working"
            ? `${client} working`
            : agentPillText(known, facts).text,
      };
    case "queued":
      return { edge: "dashed-accent", line: `Queued for ${client}` };
    case "awaiting":
      return {
        edge: "warning",
        line: note ? `Needs you: ${note}` : agentPillText(known, facts).text,
      };
    case "stale":
      return { edge: "muted", line: `${client} idle` };
    case "failed":
      return { edge: "destructive", line: null };
    default:
      // "stopping" keeps the card as it is. The pill says "Stopping".
      return NO_AGENT_LOOK;
  }
}
