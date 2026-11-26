/**
 * How a task card looks while an AI agent holds it (WS-55 EA-13g,
 * `external_agent_access.md` §12.2 item 7).
 *
 * One pure decision: a run state in, an edge and a line out. The board card
 * only applies the edge as `data-agent-edge` and draws the line. The colours
 * and the motion live in `globals.css` (`[data-agent-edge]`), from tokens.
 * The state names come from `AGENT_STATES` in `lib/statusAccent.ts`.
 *
 * | state            | edge        | line                              |
 * | working/answered | live        | "<client> · <agent_note>"         |
 * |   with a plan    | live        | "<client> · <step> · 3 of 7"      |
 * | waiting          | steady      | "Waiting on CI · about 25 min"    |
 * | queued           | steady      | none (the pill says "Queued for") |
 * | awaiting         | warning     | "Needs you: <question>"           |
 * | stale (idle)     | breathing   | the agent's last note, else none  |
 * | failed           | destructive | none (the Failed chip says it)    |
 * | no open run      | none        | none                              |
 *
 * No edge is dashed (EA-13h, owner 2026-10-30). "live" travels round the card.
 * "breathing" glows slowly and means idle. "steady" is still. The pill already
 * says "idle" or "Queued for", so the stale and queued lines never repeat it.
 *
 * SW-7b: `<client>` carries the label of the agent session when it has one,
 * "Claude Code (backend-worker-3)" (`withAgentLabel`).
 *
 * Task 1045a: "waiting" is still too, because motion means working (EA-13g).
 * The agent said that it waits on CI, a review, a deploy, a person or
 * another agent, and for about how long. The pill already names the client, and a
 * card at 1440 cuts a line that names it twice, so the line leaves it out.
 */

import {
  NOT_REPORTED,
  type Place,
  hostKindIcon,
  hostKindWord,
  placeKind,
  placeText,
} from "./agentPlace";
import {
  type AgentPillFacts,
  agentPillText,
  asAgentState,
  clientName,
  planProgressText,
  withAgentLabel,
  waitingText,
} from "./agentRuns";

export type AgentEdge = "live" | "steady" | "warning" | "breathing" | "destructive";

export interface AgentCardLook {
  edge: AgentEdge | null;
  line: string | null;
}

export const NO_AGENT_LOOK: AgentCardLook = { edge: null, line: null };

/**
 * SW-32: the line of a run with a plan ends in " · 3 of 7". The board draws
 * the step part truncated and the count apart, so a long step never hides the
 * count. Null when the line holds no count.
 */
export function splitPlanProgress(
  line: string | null,
  plan?: Parameters<typeof planProgressText>[0],
): { head: string; tail: string } | null {
  const progress = planProgressText(plan);
  if (!line || !progress) return null;
  const suffix = ` · ${progress}`;
  if (!line.endsWith(suffix)) return null;
  const head = line.slice(0, line.length - suffix.length);
  return head ? { head, tail: `· ${progress}` } : null;
}

export function agentCardLook(
  state: string | null | undefined,
  facts: AgentPillFacts = {},
): AgentCardLook {
  const known = asAgentState(state);
  if (!known) return NO_AGENT_LOOK;
  // SW-7b: "Claude Code (backend-worker-3)" when the session has a label.
  const client = withAgentLabel(clientName(facts.clientVia), facts.agentLabel);
  const note = facts.note?.trim() || null;
  switch (known) {
    case "working":
    case "answered": {
      // SW-32: a plan puts the step in progress and "3 of 7" on the line.
      const progress = planProgressText(facts.plan);
      if (progress) {
        const step = facts.plan?.step?.trim();
        return {
          edge: "live",
          line: [client, step || note, progress].filter(Boolean).join(" · "),
        };
      }
      return {
        edge: "live",
        line: note
          ? `${client} · ${note}`
          : known === "working"
            ? `${client} working`
            : agentPillText(known, facts).text,
      };
    }
    case "waiting": {
      const wait = waitingText(facts.waiting, facts.nowMs);
      return { edge: "steady", line: wait.charAt(0).toUpperCase() + wait.slice(1) };
    }
    case "queued":
      return { edge: "steady", line: null };
    case "awaiting":
      return {
        edge: "warning",
        line: note ? `Needs you: ${note}` : agentPillText(known, facts).text,
      };
    case "stale":
      return { edge: "breathing", line: note };
    case "failed":
      return { edge: "destructive", line: null };
    default:
      // "stopping" keeps the card as it is. The pill says "Stopping".
      return NO_AGENT_LOOK;
  }
}

/* ── The place line of the run panel (WS-MCP SW-106) ─────────────────────── */

/** What the run panel and the card say about where the session runs. */
export interface AgentPlaceLook {
  /** The icon of the kind of machine. */
  icon: string;
  /** "Machine: VJ laptop (Computer) · Model: Sonnet 5.5". */
  line: string;
  /** The same facts in two parts, for a tip. */
  machine: string;
  model: string;
  /** True when the session sent no machine, no kind and no model. */
  none: boolean;
}

/** The facts of a run that carry its place. Each value is untrusted text. */
export interface AgentPlaceFacts {
  agent_host?: unknown;
  agent_host_kind?: unknown;
  agent_model?: unknown;
}

/**
 * One line for the run panel: the machine with its kind, then the model. A
 * value that the agent did not send reads "Not reported". The line never
 * holds a network address. The run read gives no box proof, so the line makes
 * no "checked" claim. The Map does (`host_source` of the map read).
 */
export function agentPlaceLine(facts: AgentPlaceFacts): AgentPlaceLook {
  const place: Place = {
    host: placeText(facts.agent_host),
    kind: placeKind(facts.agent_host_kind),
    source: null,
    model: placeText(facts.agent_model),
  };
  const name = place.host ?? NOT_REPORTED;
  const kind = place.kind ? ` (${hostKindWord(place.kind)})` : "";
  const machine = `${name}${kind}`;
  const model = place.model ?? NOT_REPORTED;
  return {
    icon: hostKindIcon(place.kind),
    machine,
    model,
    line: `Machine: ${machine} \u00b7 Model: ${model}`,
    none: !place.host && !place.kind && !place.model,
  };
}
