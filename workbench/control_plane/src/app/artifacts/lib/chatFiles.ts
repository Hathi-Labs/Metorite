/**
 * The pure rules of the Artifacts app's Chats tab (D99, NS-6 slice 6c).
 *
 * Fence (R7): `chatFiles.test.ts`.
 */

import type { ChatSession } from "@/lib/sessions";

export type ArtifactsTab = "chats" | "agents";

/** The tab a link asks for. Chats unless it asks for the agents' files. */
export function artifactsTab(param: string | null | undefined): ArtifactsTab {
  return param === "agents" ? "agents" : "chats";
}

/**
 * The chat whose files show. The one the link or the member picked, when the
 * member holds it. Else the newest chat that is not archived and has a turn,
 * so the tab never opens on an empty right side when there is something to
 * show. Null when the member has no such chat.
 */
export function chatToShow(
  sessions: readonly Pick<ChatSession, "id" | "updatedAt" | "messageCount" | "archivedAt">[],
  picked: string | null,
): string | null {
  if (picked && sessions.some((s) => s.id === picked)) return picked;
  const newest = sessions
    .filter((s) => !s.archivedAt && s.messageCount > 0)
    .slice()
    .sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0))[0];
  return newest?.id ?? null;
}
