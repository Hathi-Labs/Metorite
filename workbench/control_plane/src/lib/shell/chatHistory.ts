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

/** The chat list's date groups, newest first. */
export const DATE_GROUPS = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"] as const;

/**
 * The chats by day, newest group first (CO-0, owner 2026-11-02). A flat
 * list of thirty chats reads as one wall. Topics replace the groups when
 * CO-2 ships. A group with no chat is left out.
 */
export function dateGroups<T extends Pick<ChatSession, "updatedAt">>(
  sessions: readonly T[],
  now: number,
): { label: (typeof DATE_GROUPS)[number]; items: T[] }[] {
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const today = midnight.getTime();
  const from = [today, today - DAY, today - 7 * DAY, today - 30 * DAY];
  const out = DATE_GROUPS.map((label) => ({ label, items: [] as T[] }));
  for (const s of sessions) {
    const at = Date.parse(s.updatedAt) || 0;
    const i = from.findIndex((start) => at >= start);
    out[i < 0 ? DATE_GROUPS.length - 1 : i].items.push(s);
  }
  return out.filter((g) => g.items.length > 0);
}

/** The group of the chats that the member pinned (CO-11). */
export const PINNED_GROUP = "Pinned" as const;

/** A group of the chat list: Pinned, or a day group. */
export type ChatGroupLabel = typeof PINNED_GROUP | (typeof DATE_GROUPS)[number];

/**
 * True when the member pinned the chat and it is not archived (CO-11). An
 * archive takes a chat out of Pinned. The server says the same thing, and the
 * list keeps the rule too, for a mark that this page wrote first.
 */
export function isPinned(s: Pick<ChatSession, "pinnedAt" | "archivedAt">): boolean {
  return Boolean(s.pinnedAt) && !s.archivedAt;
}

/**
 * The chat list's groups (CO-11, `chat_organization.md` §18). With the pin
 * switch on, the pinned chats come first, newest pin first, and each other
 * chat goes to its day group. A pinned chat shows once, in Pinned. With the
 * switch off this is `dateGroups`, so the list draws as before.
 */
export function chatGroups<T extends Pick<ChatSession, "updatedAt" | "pinnedAt" | "archivedAt">>(
  sessions: readonly T[],
  now: number,
  pinsOn: boolean,
): { label: ChatGroupLabel; items: T[] }[] {
  if (!pinsOn) return dateGroups(sessions, now);
  const at = (s: T) => Date.parse(s.pinnedAt ?? "") || 0;
  const pinned = sessions.filter(isPinned).sort((a, b) => at(b) - at(a));
  const rest = sessions.filter((s) => !isPinned(s));
  const groups: { label: ChatGroupLabel; items: T[] }[] = dateGroups(rest, now);
  return pinned.length > 0 ? [{ label: PINNED_GROUP, items: pinned }, ...groups] : groups;
}

/**
 * A group of the chat list with topics (CO-2b): Pinned, a topic, or a day.
 * `topicId` is set on a topic group only. It is the group's key, apart from
 * its label: a company topic and a private topic can share a name, and a
 * topic can be called "Today".
 */
export interface ChatGroup<T> {
  label: string;
  items: T[];
  topicId?: string;
}

/**
 * The topic of a chat that the list shows, or null (CO-2b). The server sends
 * a topic only when it shows to the member, so a chat in a hidden topic comes
 * with null and sits under its day. A chat with an id and no name sits there
 * too.
 */
export function shownTopic(s: Pick<ChatSession, "topicId" | "topicName">): { id: string; name: string } | null {
  const id = s.topicId ?? "";
  const name = s.topicName ?? "";
  return id && name.trim() ? { id, name } : null;
}

/**
 * The chat list's groups with topics (CO-2b, `chat_organization.md` §6).
 * With the topic switch on: Pinned first, as `chatGroups` orders it, then one
 * group for each topic, then the day groups of the chats with no topic. The
 * topics go by name, with no case, then by id, so the order is the same on
 * each device. Inside a topic the newest chat comes first. A pinned chat
 * shows once, in Pinned. With the switch off this is `chatGroups`.
 */
export function topicGroups<
  T extends Pick<ChatSession, "updatedAt" | "pinnedAt" | "archivedAt" | "topicId" | "topicName">,
>(sessions: readonly T[], now: number, topicsOn: boolean, pinsOn: boolean): ChatGroup<T>[] {
  if (!topicsOn) return chatGroups(sessions, now, pinsOn);
  const pinned = pinsOn ? sessions.filter(isPinned) : [];
  const rest = pinsOn ? sessions.filter((s) => !isPinned(s)) : [...sessions];
  const at = (s: T) => Date.parse(s.updatedAt) || 0;
  const byTopic = new Map<string, ChatGroup<T>>();
  const loose: T[] = [];
  for (const s of [...rest].sort((a, b) => at(b) - at(a))) {
    const topic = shownTopic(s);
    if (!topic) {
      loose.push(s);
      continue;
    }
    const group = byTopic.get(topic.id) ?? { label: topic.name, items: [], topicId: topic.id };
    group.items.push(s);
    byTopic.set(topic.id, group);
  }
  const topics = [...byTopic.values()].sort(
    (a, b) =>
      a.label.localeCompare(b.label, "en", { sensitivity: "base" }) ||
      (a.topicId! < b.topicId! ? -1 : a.topicId! > b.topicId! ? 1 : 0),
  );
  // Pinned keeps the order of CO-11: `chatGroups` of the pinned chats alone.
  const head: ChatGroup<T>[] = pinned.length > 0 ? chatGroups(pinned, now, true) : [];
  return [...head, ...topics, ...dateGroups(loose, now)];
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
