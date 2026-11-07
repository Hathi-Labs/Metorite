/**
 * The rules of the list "For you" on the page `/waiting` (WS-AP AP-2a,
 * `waiting_on_me.md` §5.2). Pure, so the fences read them.
 *
 * Two reads fill the list:
 *
 * 1. The read seam, `GET /api/shell/waiting` (§7.3, AP-1a). It gives four
 *    kinds: `decision`, `chat_ask`, `app_request` and `held_write`. Each row
 *    is `{id, kind, title, waiter, href, since, acts}`, and each act is a
 *    link. A 404 means `WAITING_ORGS` does not hold the org: the OFF state.
 * 2. The member's own agent questions, `agentRunsApi.waiting()`
 *    (`waitingForYou.ts`). AP-1b moves them into the seam later.
 *
 * The rules:
 *
 * - **Oldest first, across both reads.** A row with no time goes last.
 * - **A failed source reads failed.** It names what could not load, and it
 *   never draws as an empty list. So "Nothing waits on you" shows only when
 *   every read answered.
 * - **The OFF state is not the empty state.** A 404 from the seam says that
 *   the list is not on, never that nothing waits.
 * - **A `chat_ask` row shows its fixed title only.** A model wrote the
 *   question, so the page never draws it, whatever the wire holds.
 * - **Each link stays in the app** (`isInAppPath`). A row or an act whose
 *   href leaves the app draws no link.
 * - **The text is data.** `title` and `waiter` draw as text, never markup.
 *
 * Fence: `forYou.test.ts`.
 */

import { isInAppPath } from "@/lib/inAppLink";

import { askedAt, type WaitingRun } from "./waitingForYou";

/** The kinds of the seam in AP-1a. */
export type WaitKind = "decision" | "chat_ask" | "app_request" | "held_write";

export const SEAM_KINDS: readonly WaitKind[] = ["decision", "chat_ask", "app_request", "held_write"];

/** The fixed title of a chat question (`CHAT_ASK_TITLE` of `waiting.py`). */
export const CHAT_ASK_TITLE = "An agent asked you a question in a chat";

/** How each kind is named in a line about it. */
export const KIND_WORDS: Record<WaitKind, { label: string; plural: string }> = {
  decision: { label: "Decision", plural: "decisions" },
  chat_ask: { label: "Chat question", plural: "chat questions" },
  app_request: { label: "App request", plural: "app requests" },
  held_write: { label: "Held write", plural: "held writes" },
};

/** The words of the agent questions, in a failed line. */
export const AGENT_QUESTIONS = "agent questions";

/** One act of a row: a link that opens the owning app. */
export interface WaitAct {
  id: string;
  label: string;
  href: string;
}

/** One row of the seam, read defensively. */
export interface Wait {
  id: string;
  kind: WaitKind;
  title: string;
  waiter: string;
  /** The in-app link of the subject, or null when the wire gave none. */
  href: string | null;
  since: string | null;
  acts: WaitAct[];
}

export type SourceState = "ok" | "absent" | "failed";

export interface WaitList {
  items: Wait[];
  sources: Partial<Record<WaitKind, SourceState>>;
}

/** What one read of the seam gave: the OFF state (404), or a list. */
export type SeamRead = { off: true } | { off: false; list: WaitList };

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isKind(value: unknown): value is WaitKind {
  return typeof value === "string" && (SEAM_KINDS as readonly string[]).includes(value);
}

function inApp(value: unknown): string | null {
  const href = text(value);
  return href && isInAppPath(href) ? href : null;
}

function readActs(raw: unknown): WaitAct[] {
  if (!Array.isArray(raw)) return [];
  const out: WaitAct[] = [];
  for (const a of raw) {
    if (!a || typeof a !== "object") continue;
    const act = a as Record<string, unknown>;
    // AP-1a has no tap act and no act that writes. A way other than `open`
    // has no client here, so it draws nothing.
    if (act.way !== undefined && act.way !== "open") continue;
    const href = inApp(act.href);
    const label = text(act.label);
    if (!href || !label) continue;
    out.push({ id: text(act.id) || label, label, href });
  }
  return out;
}

/** One row of the wire, or null when it is not a row of a known kind. */
function readWait(raw: unknown): Wait | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  if (!isKind(row.kind)) return null;
  const id = text(row.id);
  if (!id) return null;
  return {
    id,
    kind: row.kind,
    // The question of a chat card stays out, whatever the wire holds.
    title: row.kind === "chat_ask" ? CHAT_ASK_TITLE : text(row.title) || "(untitled)",
    waiter: text(row.waiter) || "Somebody",
    href: inApp(row.href),
    since: text(row.since) || null,
    acts: readActs(row.acts),
  };
}

/** Read the seam's answer. A field the server left out reads as empty. */
export function readWaitList(raw: unknown): WaitList {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const items = Array.isArray(body.items)
    ? body.items.map(readWait).filter((w): w is Wait => w !== null)
    : [];
  const sources: Partial<Record<WaitKind, SourceState>> = {};
  const rawSources = body.sources && typeof body.sources === "object" ? body.sources : {};
  for (const [kind, state] of Object.entries(rawSources as Record<string, unknown>)) {
    if (isKind(kind) && (state === "ok" || state === "absent" || state === "failed")) {
      sources[kind] = state;
    }
  }
  return { items, sources };
}

/**
 * Read the seam through the BFF. A 404 is the OFF state. Any other refusal
 * or failure throws, so the page shows that the list could not load.
 */
export async function fetchWaiting(): Promise<SeamRead> {
  const res = await fetch("/api/shell/waiting", { cache: "no-store" });
  if (res.status === 404) return { off: true };
  if (!res.ok) throw new Error("What waits on you could not load.");
  return { off: false, list: readWaitList(await res.json()) };
}

/** The kinds whose source failed, in `SEAM_KINDS` order. */
export function failedKinds(list: WaitList): WaitKind[] {
  return SEAM_KINDS.filter((k) => list.sources[k] === "failed");
}

/** "a", "a and b", "a, b and c". */
function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/**
 * The line for what could not load: "Could not load decisions and agent
 * questions." Null when everything loaded. The page adds "Try again" as
 * a button beside it.
 */
export function failedLine(kinds: readonly WaitKind[], agentFailed: boolean): string | null {
  const words = kinds.map((k) => KIND_WORDS[k].plural);
  if (agentFailed) words.push(AGENT_QUESTIONS);
  return words.length ? `Could not load ${joinWords(words)}.` : null;
}

/** One entry of the merged list. */
export type Entry =
  | { type: "wait"; key: string; at: string | null; wait: Wait }
  | { type: "agent"; key: string; at: string | null; run: WaitingRun };

function ms(at: string | null): number {
  const t = at ? Date.parse(at) : NaN;
  return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : t;
}

/**
 * The seam's rows and the agent questions in one list, oldest first. A row
 * with no time goes last. Equal times keep the order of the wire, seam rows
 * first.
 */
export function mergeOldestFirst(waits: readonly Wait[], runs: readonly WaitingRun[]): Entry[] {
  const entries: Entry[] = [
    ...waits.map((wait): Entry => ({ type: "wait", key: `wait:${wait.id}`, at: wait.since, wait })),
    ...runs.map((run): Entry => ({ type: "agent", key: `agent:${run.id}`, at: askedAt(run), run })),
  ];
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => ms(a.entry.at) - ms(b.entry.at) || a.index - b.index)
    .map((x) => x.entry);
}

/** How one read stands. `null` data means it has not answered yet. */
export interface ReadState<T> {
  data: T | undefined;
  error: string | null;
}

/** What the page draws under its title. */
export type PageView =
  /** Nothing answered yet: the skeleton. */
  | "loading"
  /** At least one row. A failed line and the off note may show above it. */
  | "list"
  /** No row, and a read failed: the failed line only, never "Nothing waits". */
  | "failed"
  /** No row, and the seam is off (404): the off state. */
  | "off"
  /** No row, and every read answered: "Nothing waits on you." */
  | "empty";

export interface ForYou {
  view: PageView;
  entries: Entry[];
  /** "Could not load …", or null. */
  failed: string | null;
  /** The seam answered 404: the list of the four kinds is not on. */
  off: boolean;
}

/**
 * The whole rule of the page, from its two reads. `agent.data` null means
 * the agent-run routes are off (404), and then no agent question shows.
 */
export function forYou(seam: ReadState<SeamRead>, agent: ReadState<WaitingRun[] | null>): ForYou {
  const seamOn = seam.data && !seam.data.off ? seam.data.list : null;
  const off = seam.data?.off === true;
  const runs = agent.data ?? [];
  const entries = mergeOldestFirst(seamOn?.items ?? [], runs);
  // A read that failed is a failed read, for every kind it carries. One that
  // failed over rows it already gave keeps its rows (the read cache keeps
  // them), and still says so. A failed read over the OFF state says nothing
  // more: the list is off.
  let seamFailedKinds: WaitKind[] = [];
  if (seam.error !== null) seamFailedKinds = off ? [] : [...SEAM_KINDS];
  else if (seamOn) seamFailedKinds = failedKinds(seamOn);
  const failed = failedLine(seamFailedKinds, agent.error !== null);
  const waiting = (seam.data === undefined && seam.error === null) || (agent.data === undefined && agent.error === null);
  let view: PageView;
  if (entries.length > 0) view = "list";
  else if (failed) view = "failed";
  else if (waiting) view = "loading";
  else if (off) view = "off";
  else view = "empty";
  return { view, entries, failed, off };
}
