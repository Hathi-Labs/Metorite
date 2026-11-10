import { Email } from "./types";

/**
 * Format a date for email list display.
 * - < 24h ago: time only (e.g. "2:30 PM")
 * - < 48h ago: "Yesterday"
 * - older: short date (e.g. "Jun 15")
 */
export function timeLabel(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const hours = diffMs / (1000 * 60 * 60);

  if (hours < 24) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (hours < 48) return "Yesterday";
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

/**
 * Format full date for email detail view.
 * e.g. "Wednesday, June 17, 2026 at 2:30 PM"
 */
export function fullDateLabel(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString([], {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "14 Sep" in the member's own time zone, with the year when it is not the
 * year of `now`. Month names come from a fixed list, so the text does not
 * change with the version of the runtime's date data.
 *
 * The import panel (`lib/onboarding.ts`, EM-T6d) and the storage notice
 * (`lib/storage.ts`, EM-T6e) both read it, so it lives here and neither
 * module imports the other for it. `onboarding.ts` re-exports it.
 */
export function shortDate(d: Date, now: Date): string {
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

/**
 * Get initials from a name for avatar display.
 * e.g. "Alex Morgan" → "AM", "Priya Sharma" → "PS"
 */
export function initials(name: string): string {
  return name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/**
 * Get the folder label for display.
 */
export function folderLabel(folder: string): string {
  const map: Record<string, string> = {
    all: "All",
    inbox: "Inbox",
    starred: "Starred",
    sent: "Sent",
    drafts: "Drafts",
    archive: "Archive",
    junk: "Junk",
    spam: "Spam",
    trash: "Trash",
  };
  return map[folder] || folder;
}

/**
 * Truncate text with ellipsis.
 */
export function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + "…";
}

/**
 * Check if an email is unread.
 */
export function isUnread(email: Email): boolean {
  return !email.isRead;
}

/**
 * Build a local, optimistic "sent" message to drop into the open thread the
 * instant a reply is sent — the provider copy only lands on the next sync (and
 * Outlook's send returns no id at all), so without this the reply briefly
 * vanishes from the conversation. Carries a `local-sent-*` id; the thread merge
 * drops it once the real synced message arrives.
 */
export function buildOptimisticSent(params: {
  accountId: string;
  threadId?: string;
  fromEmail: string;
  to: string[];
  cc?: string[];
  subject: string;
  bodyText: string;
  hasAttachments?: boolean;
}): Email {
  const now = new Date().toISOString();
  return {
    id: `local-sent-${Date.now()}`,
    providerMessageId: "",
    threadId: params.threadId,
    accountId: params.accountId,
    from: { name: "You", email: params.fromEmail },
    to: params.to.map((e) => ({ name: "", email: e })),
    cc: (params.cc || []).map((e) => ({ name: "", email: e })),
    subject: params.subject,
    bodyText: params.bodyText,
    bodyTruncated: false,
    snippet: params.bodyText.replace(/\s+/g, " ").trim().slice(0, 140),
    hasAttachments: !!params.hasAttachments,
    isRead: true,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    labels: [],
    categories: [],
    folder: "sent",
    receivedAt: now,
    syncedAt: now,
  };
}

/** First-N-chars normalized key of a body, for matching an optimistic sent
 *  message to its real synced counterpart (so the optimistic copy is dropped). */
export function bodyMatchKey(text: string): string {
  return (text || "").replace(/\s+/g, " ").trim().toLowerCase().slice(0, 60);
}

/** Human-readable byte size for an attachment (B / KB / MB). */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
