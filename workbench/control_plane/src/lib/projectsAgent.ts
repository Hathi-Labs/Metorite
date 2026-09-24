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
