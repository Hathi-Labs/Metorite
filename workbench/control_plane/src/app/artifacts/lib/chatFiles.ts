/**
 * The pure rules of the Artifacts app's Chats tab (D99, NS-6 slice 6c).
 *
 * Fence (R7): `chatFiles.test.ts`.
 */

import type { ChatSession } from "@/lib/sessions";
import {
  workspaceFileOfHref,
  workspaceFileUrl,
  type WorkspaceFileRef,
} from "@/lib/artifactKind";

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

/**
 * The file a link asks the viewer to open: `?session=<id>&file=<path>`
 * (owner report, 2026-10-16). The proxy route sends a direct open of a
 * Markdown file here, and a chat link to a file carries this address. Null
 * without both values, or for a session id that the one link parser
 * (`workspaceFileOfHref`) refuses. The gateway still decides who may read it.
 */
export function linkedFile(
  session: string | null | undefined,
  file: string | null | undefined,
): WorkspaceFileRef | null {
  if (!session || !file) return null;
  return workspaceFileOfHref(workspaceFileUrl(session, file), "https://in-app.invalid");
}
