/**
 * Projects · the notification bell's pure half (WS-27j).
 *
 * How a row reads, how the badge counts, and how a comment box turns a picked
 * colleague into a mention the gateway will actually parse. All plain functions
 * over plain data, so the parts that are easy to get subtly wrong — the badge
 * that says 0 when there are unread rows, the mention token that silently fails
 * to match — are provable without a browser.
 */

import { clientName } from "./agentRuns";
import type { NotificationRow } from "./api";

/** Above this the badge stops counting and starts gesturing. */
export const BADGE_MAX = 99;

/**
 * What the badge shows, or null when it should not be drawn at all.
 *
 * Null rather than `"0"`: a bell wearing a zero is a bell that always looks
 * like it wants something, which is how people learn to stop looking at it.
 */
export function badge(unread: number): string | null {
  if (!Number.isFinite(unread) || unread <= 0) return null;
  return unread > BADGE_MAX ? `${BADGE_MAX}+` : String(unread);
}

/**
 * The unread badge, split (WS-27v): `mentions` is the subset of `total` whose
 * reason is a mention — "you were named", the stronger claim on attention.
 */
export interface UnreadSplit {
  total: number;
  mentions: number;
}

const sane = (n: unknown): number =>
  typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;

/**
 * Normalise the server's unread payload into a split the bell can draw.
 *
 * Tolerates the pre-WS-27v bare number (a stale gateway behind a fresh UI is
 * a deploy-window reality, and a bell that crashes during it notifies nobody
 * about anything) and clamps `mentions` to `total`, because a subset larger
 * than its set is a payload bug the badge must not amplify.
 */
export function unreadSplit(
  raw: number | Partial<UnreadSplit> | null | undefined,
): UnreadSplit {
  if (typeof raw === "number") return { total: sane(raw), mentions: 0 };
  const total = sane(raw?.total);
  return { total, mentions: Math.min(sane(raw?.mentions), total) };
}

/**
 * The split after one row of `kind` is read — the optimistic update the bell
 * applies before the server confirms. Mentions only shrink when the row read
 * was a mention; both floors are zero so a double-click cannot go negative.
 */
export function afterRead(split: UnreadSplit, kind: string): UnreadSplit {
  return {
    total: Math.max(0, split.total - 1),
    mentions:
      kind === "mention"
        ? Math.max(0, split.mentions - 1)
        : Math.min(split.mentions, Math.max(0, split.total - 1)),
  };
}

/** The verb for each kind. */
const VERB: Record<string, string> = {
  assigned: "assigned you",
  mention: "mentioned you on",
  comment: "commented on",
  // WS-55 EA-12c — an outside agent asks the member on a task (§12.1).
  agent_question: "asked you about",
};

/**
 * One line of prose for a notification.
 *
 * The actor is shown as the local part of their address — "priya", not
 * "priya@fracktal.in". A bell is a glance, and a column of full addresses is a
 * column of one repeated domain.
 */
export function describe(row: NotificationRow): string {
  const who = actorLabel(row.actor);
  const verb = VERB[row.kind] ?? "updated";
  const task = taskLabel(row);
  return `${who} ${verb} ${task}`;
}

export function actorLabel(actor: string | null | undefined): string {
  const raw = (actor ?? "").trim();
  if (!raw) return "somebody";
  // An agent keeps its full handle: `agent:builder` reads as a name already,
  // and splitting it at the colon would render every agent as "agent".
  if (raw.startsWith("agent:")) return raw.slice("agent:".length) || raw;
  // WS-55 EA-12c — an outside agent's stamp, `mcp:claude-code`, reads
  // "Claude Code" (EA12-8 sets it as the actor of `agent_question`).
  if (raw.startsWith("mcp:")) return clientName(raw);
  return raw.split("@")[0] || raw;
}

/** `#12 Fix the extruder`, or a fallback when the task has no number yet. */
export function taskLabel(row: NotificationRow): string {
  const title = (row.task_title ?? "").trim() || "a task";
  return row.task_number ? `#${row.task_number} ${title}` : title;
}

/** Where clicking a notification lands. */
export function linkTo(row: NotificationRow): string {
  return `/projects?task=${encodeURIComponent(row.task_id)}`;
}

/** Unread rows first, then newest first — the order a bell is read in. */
export function order(rows: NotificationRow[]): NotificationRow[] {
  return [...rows].sort((a, b) => {
    const unreadA = a.read_at ? 1 : 0;
    const unreadB = b.read_at ? 1 : 0;
    if (unreadA !== unreadB) return unreadA - unreadB;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });
}

export const unreadIds = (rows: NotificationRow[]): string[] =>
  rows.filter((r) => !r.read_at).map((r) => r.id);

/**
 * Insert an @mention for `email` into a comment at `caret`.
 *
 * The token is `@address`, because that is the only form the gateway parses —
 * migration 148 dropped `UNIQUE(name)` on the argument that two real people
 * share a name, so `@Priya` has no answer and guessing would ping the wrong
 * person. Everything about this function exists so nobody has to type it.
 *
 * Returns the new body and where the caret should land, because leaving the
 * caret behind the inserted text is how a picker produces `@a@b.co ping` typed
 * as `ping@a@b.co`.
 */
export function insertMention(
  body: string,
  caret: number,
  email: string,
): { body: string; caret: number } {
  const address = (email ?? "").trim().toLowerCase();
  if (!address) return { body, caret };
  const at = Math.max(0, Math.min(caret, body.length));
  const before = body.slice(0, at);
  const after = body.slice(at);
  // One space each side, and never two: a mention pushed against the previous
  // word (`ping@a@b.co`) does not match the gateway's pattern at all.
  const lead = before && !/\s$/.test(before) ? " " : "";
  const trail = after.startsWith(" ") ? "" : " ";
  const token = `${lead}@${address}${trail}`;
  return { body: before + token + after, caret: at + token.length };
}

/**
 * The addresses a body currently mentions.
 *
 * Deliberately the SAME pattern as the gateway's `_MENTION`. A composer that
 * highlighted names the server does not parse would promise notifications
 * nobody receives — the failure this whole ticket exists to end.
 */
const MENTION = /@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;

export function mentionsIn(body: string): string[] {
  const seen = new Set<string>();
  for (const match of (body ?? "").matchAll(MENTION)) {
    seen.add(match[1].toLowerCase());
  }
  return [...seen];
}

/**
 * The sentence shown when a comment or assignment reached somebody's name but
 * not their bell.
 *
 * Said out loud rather than swallowed: the author has just tried to pull a
 * colleague in, and silence would leave them believing it worked.
 */
export function notDeliveredNotice(skipped: string[]): string | null {
  if (!skipped?.length) return null;
  const names = skipped.map(actorLabel).join(", ");
  return `Not notified: ${names} — they cannot see this project.`;
}
