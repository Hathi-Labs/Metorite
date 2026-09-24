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
  if (turn.authorKind === "agent" && turn.authorEmail && turn.authorEmail !== PROJECTS_AGENT) {
    return false;
  }
  return true;
}
