import { agentSlug } from "@/lib/agentName";

/**
 * The Projects assistant's agent name, in one place.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.2 and §15.
 *
 * The rail scopes its sessions by it (`AssistantRail.tsx`), and the chat
 * turns the entity pills on for it and for no other agent (`AgentChat.tsx`,
 * WS-27bm S9 fix round 1). A pill resolves against Projects tool output, so
 * another agent's «text» or email address must stay what it was.
 */
export const PROJECTS_AGENT = "projects-assistant";

/**
 * Who wrote an assistant turn: the agent the turn was SENT to.
 *
 * Stamped on the turn when it starts to stream (S9 fix round 3), because
 * nothing else does: `chatStream` sets no author, and the server stamps
 * `author_email` only when it saves the row (`routes/chat.py::_attribute`).
 * Without it, a member who talks to one agent and then switches the thread
 * to the Projects assistant sees every earlier answer redrawn with pills.
 * `sessions.ts` sends the stamp with the row, and the server keeps the first
 * stamp it gets (`COALESCE`), so a reload reads the same name.
 */
export function agentAuthor(agentName: string): { authorKind: "agent"; authorEmail: string } {
  return { authorKind: "agent", authorEmail: agentName };
}

/**
 * True when one turn may draw entity pills. `threadIsProjects` is the
 * caller's `agentName === PROJECTS_AGENT`. In a room two agents can speak in
 * one thread, and `authorEmail` names the agent of this turn: a turn by any
 * other agent draws no pills. Exported for its test.
 */
export function pillsForTurn(
  threadIsProjects: boolean,
  turn: { authorKind?: string; authorEmail?: string | null },
): boolean {
  if (!threadIsProjects) return false;
  // Whenever a turn names its author, the author must be this agent. A turn
  // with no author at all is a row saved before attribution existed.
  if (turn.authorEmail && turn.authorEmail !== PROJECTS_AGENT) return false;
  if (turn.authorKind && turn.authorKind !== "agent") return false;
  return true;
}

/**
 * The agents whose answers name Projects tasks by number (owner report,
 * 2026-10-13). Their bare `#171` draws as the task pill when it is a task
 * (`ChatTaskRef.tsx`). Every task is one `pm_tasks` row, so the My Tasks
 * assistant names the same tasks. Another agent's `#4521` is an invoice or an
 * issue number, so it stays text.
 *
 * The orchestrator is on the list (owner report, 2026-10-19), with a
 * stricter rule (`taskRefModeForTurn`). Since D99 it is the one assistant:
 * the main chat and the assistant dock run it, and it asks the Projects
 * assistant and then repeats what that agent wrote. Its answer "#234 Spec
 * tool-gap detection" drew as text. `metorite` is the old name of the same
 * agent, still on old sessions.
 */
export const TASK_REF_AGENTS: ReadonlySet<string> = new Set([
  PROJECTS_AGENT,
  "task-manager",
  "orchestrator",
  "metorite",
]);

/** The agents that own tasks. Their answers link every bare `#171`. */
export const TASK_AGENTS: ReadonlySet<string> = new Set([PROJECTS_AGENT, "task-manager"]);

/** The agents that relay other agents' text: the orchestrator, by both names. */
export const RELAY_AGENTS: ReadonlySet<string> = new Set(["orchestrator", "metorite"]);

/** How one turn links its bare `#171`s: not at all, always, or only when
 *  the words after the number are the task's title (`"echo"`). */
export type TaskRefMode = boolean | "echo";

/** The parts of a tool event that name the agent a hand-off asked. */
interface HandOff {
  name?: string;
  subAgentName?: string;
  args?: Record<string, unknown>;
}

/** True when one of the turn's tools asked a task agent (`TASK_AGENTS`).
 *  The names read as `paneForDelegate` reads them (`agentSlug`). */
export function askedTaskAgent(events: readonly HandOff[] | undefined): boolean {
  return (events ?? []).some((e) =>
    [e.subAgentName, e.name, e.args?.agent_name, e.args?.agent].some(
      (n) => typeof n === "string" && TASK_AGENTS.has(agentSlug(n)),
    ),
  );
}

/**
 * How one turn links its bare task references (review of PR #1156, P1).
 *
 * - A task agent's turn (`TASK_AGENTS`) links every bare `#171`, as before.
 * - The orchestrator relays email, CRM and plain text too. There a bare
 *   "Ticket #48 from Acme" or "Option #1" is no task, and the parser leaves
 *   "issue", "ticket", "item" and "option" to the lookup on purpose. So an
 *   orchestrator turn links every bare number only when it asked a task
 *   agent (the owner's turn asked the Projects assistant). Any other
 *   orchestrator turn links a number only when the words after it are the
 *   task's title (`"echo"`, `echoesTitle`).
 * - Any other turn links nothing (`taskRefsForTurn`).
 */
export function taskRefModeForTurn(
  threadAgent: string | undefined,
  turn: { authorKind?: string; authorEmail?: string | null },
  events?: readonly HandOff[],
): TaskRefMode {
  if (!taskRefsForTurn(threadAgent, turn)) return false;
  const author = turn.authorEmail || threadAgent || "";
  if (!RELAY_AGENTS.has(author)) return true;
  return askedTaskAgent(events) ? true : "echo";
}

/**
 * True when one turn may link its bare task references. `threadAgent` is the
 * caller's agent. As with `pillsForTurn`, a turn that names another author
 * must name one of `TASK_REF_AGENTS`, and a human turn links nothing.
 * Exported for its test.
 */
export function taskRefsForTurn(
  threadAgent: string | undefined,
  turn: { authorKind?: string; authorEmail?: string | null },
): boolean {
  if (!threadAgent || !TASK_REF_AGENTS.has(threadAgent)) return false;
  if (turn.authorKind && turn.authorKind !== "agent") return false;
  if (turn.authorEmail && !TASK_REF_AGENTS.has(turn.authorEmail)) return false;
  return true;
}
