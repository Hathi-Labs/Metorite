/**
 * The session picker of the one assistant (D99, `navigation_shell.md` §7.1),
 * shaped as Claude Code's in an editor (owner, 2026-10-12): one list, newest
 * first, each chat with a short age ("now", "4m", "1d"), and the archived
 * chats under their own fold with a count. Pure, so the tests read it.
 *
 * Every chat of the member shows, whatever its agent. A chat records its
 * agent, so an old chat with a specialist still opens with that specialist.
 *
 * Fence (R7): `chatHistory.test.ts`.
 */

import type { FileEntry } from "@/components/ArtifactSidebar";
import type { ChatSession } from "@/lib/sessions";

/**
 * A file the chat wrote, merged into the files it already listed. One row
 * per path: a create and an update of one file are one row, and a later
 * event that leaves out the size or the type keeps the earlier one. The
 * same rule as `/chat` (`app/chat/page.tsx`, `onArtifact`).
 */
export function mergeFileEntry(
  prev: readonly FileEntry[],
  entry: { path: string; size?: number; mimeType?: string },
  modifiedAt: string,
): FileEntry[] {
  if (!entry.path) return [...prev];
  const prior = prev.find((f) => f.path === entry.path);
  const next: FileEntry = {
    path: entry.path,
    name: entry.path.split("/").pop() ?? entry.path,
    size: entry.size ?? prior?.size ?? 0,
    modified_at: modifiedAt,
    mime_type: entry.mimeType ?? prior?.mime_type ?? "",
  };
  return [...prev.filter((f) => f.path !== entry.path), next];
}

/** The Artifacts address of one chat's files (NS-6 slice 6c). */
export function chatFilesHref(sessionId: string): string {
  return `/artifacts?session=${encodeURIComponent(sessionId)}`;
}

/** A chat's name in the list: the member's name for it, else its first words. */
export function sessionTitle(s: Pick<ChatSession, "title">): string {
  return s.title?.trim() || "New chat";
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/**
 * A chat's age, short: "now" under a minute, then minutes, hours, days and
 * weeks. From 8 weeks it is the date, "12 Oct". A time in the future (a clock
 * that runs fast) reads "now".
 */
export function relativeAge(updatedAt: string, now: number): string {
  const at = Date.parse(updatedAt);
  if (!Number.isFinite(at)) return "";
  const gone = now - at;
  if (gone < MIN) return "now";
  if (gone < HOUR) return `${Math.floor(gone / MIN)}m`;
  if (gone < DAY) return `${Math.floor(gone / HOUR)}h`;
  if (gone < 7 * DAY) return `${Math.floor(gone / DAY)}d`;
  if (gone < 56 * DAY) return `${Math.floor(gone / (7 * DAY))}w`;
  return new Date(at).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/**
 * The words a search matches: the name and the last preview, folded. A
 * blank query keeps every chat.
 */
export function matchesQuery(s: Pick<ChatSession, "title" | "lastPreview">, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return `${sessionTitle(s)} ${s.lastPreview ?? ""}`.toLowerCase().includes(q);
}

export interface SessionLists {
  /** The chats in the list, newest first. */
  open: ChatSession[];
  /** The archived chats, newest first. */
  archived: ChatSession[];
}

/**
 * The picker's two lists, each newest first, after the search. An empty chat
 * is dropped unless it is `keepId`, the chat on screen, because a list of
 * "New chat" rows hides the real ones.
 */
export function splitSessions(
  sessions: readonly ChatSession[],
  query = "",
  keepId: string | null = null,
): SessionLists {
  const shown = sessions
    .filter((s) => s.messageCount > 0 || s.id === keepId)
    .filter((s) => matchesQuery(s, query))
    .slice()
    .sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0));
  return {
    open: shown.filter((s) => !s.archivedAt),
    archived: shown.filter((s) => Boolean(s.archivedAt)),
  };
}
