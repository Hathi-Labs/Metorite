/**
 * runActivity — which app a live agent run belongs to, and how many run on
 * each app. Pure functions. The hook that feeds them is `useRunActivity` in
 * `hooks/useActiveSessions.ts`.
 *
 * ONE mapping. A pane declares its agent in the shell manifest
 * (`NavPane.agent` in `lib/nav.ts`, `navigation_shell.md` §5.1). The first
 * pane that names an agent owns its runs. An agent that no pane names, the
 * orchestrator among them, counts on Chat (`CHAT_HREF`). So does a run whose
 * pane the member cannot see, because a run must never be invisible.
 *
 * A run whose state is `needs_input` (WS-51 S2) waits on the member. It
 * counts in both counts, and `runBadge` draws it amber, over the green.
 *
 * Owning spec: `project-docs/specs/chat_run_continuity.md` §4 S1 and S2.
 * Fence (R7): `runActivity.test.ts`.
 */

import { NAV_SECTIONS, type NavSection } from "@/lib/nav";

/** The catch-all app: the general chat. */
export const CHAT_HREF = "/chat";

/** The app pane for an agent, from the manifest's `agent` field. */
export function appForAgent(
  agentName: string | null | undefined,
  sections: readonly NavSection[] = NAV_SECTIONS,
): string {
  if (agentName) {
    for (const section of sections) {
      for (const pane of section.items) {
        if (pane.agent === agentName) return pane.href;
      }
    }
  }
  return CHAT_HREF;
}

/**
 * One live run: its thread, the agent that runs it if known, and its state.
 * `needs_input` (WS-51 S2) is a run that waits on the member's answer.
 */
export type RunRef = {
  threadId: string;
  agentName?: string | null;
  state?: "running" | "needs_input";
};

/**
 * The count of live runs on each app pane.
 *
 * `visibleHrefs` is the set of panes the member's nav shows. A run whose pane
 * is not in it counts on Chat. Pass `null` to skip that fold. `onlyNeedsInput`
 * counts only the runs that wait on the member (WS-51 S2), on the same fold.
 */
export function countRunsByApp(
  runs: readonly RunRef[],
  visibleHrefs: ReadonlySet<string> | null = null,
  sections: readonly NavSection[] = NAV_SECTIONS,
  onlyNeedsInput = false,
): Record<string, number> {
  const counts: Record<string, number> = {};
  const seen = new Set<string>();
  for (const run of runs) {
    if (seen.has(run.threadId)) continue;
    seen.add(run.threadId);
    if (onlyNeedsInput && run.state !== "needs_input") continue;
    let href = appForAgent(run.agentName, sections);
    if (visibleHrefs && !visibleHrefs.has(href)) href = CHAT_HREF;
    counts[href] = (counts[href] ?? 0) + 1;
  }
  return counts;
}

/**
 * Join the server's runs with the runs this tab streams. The server's agent
 * name wins unless it is "unknown" (a run with no session row yet). Then the
 * tab's own record of the agent fills it in.
 */
export function mergeRuns(
  server: readonly RunRef[],
  localIds: Iterable<string>,
  localAgent: (threadId: string) => string | undefined,
): RunRef[] {
  const out = new Map<string, RunRef>();
  for (const r of server) {
    const named = r.agentName && r.agentName !== "unknown" ? r.agentName : localAgent(r.threadId);
    out.set(r.threadId, {
      threadId: r.threadId,
      agentName: named ?? r.agentName ?? null,
      // Only the server knows that a run waits on the member.
      state: r.state === "needs_input" ? "needs_input" : "running",
    });
  }
  for (const id of localIds) {
    if (!out.has(id)) out.set(id, { threadId: id, agentName: localAgent(id) ?? null });
  }
  return [...out.values()];
}

/** The badge's spoken name: "1 assistant running", "2 assistants running". */
export function runningLabel(n: number): string {
  return `${n} ${n === 1 ? "assistant" : "assistants"} running`;
}

/** The amber badge's spoken name: "1 assistant needs your answer". */
export function needsInputLabel(n: number): string {
  return n === 1 ? "1 assistant needs your answer" : `${n} assistants need your answer`;
}

/** The blue badge's spoken name: "1 new reply", "2 new replies" (WS-51 S5). */
export function newReplyLabel(n: number): string {
  return n === 1 ? "1 new reply" : `${n} new replies`;
}

/** What a nav entry wears for its runs: a count, a tone and a spoken name. */
export type RunBadge = { count: number; tone: "success" | "warning" | "info"; label: string };

/**
 * The ONE rule for a pane's run badge (WS-51 S2, S5). The order is what the
 * member must do, first to last:
 *
 *   1. a run waits on the member: amber (`warning`), the count of those runs;
 *   2. a reply waits to be read: blue (`info`), the count of unread chats;
 *   3. runs go on: green (`success`), the count of every run.
 *
 * The tab title keeps the same order ("Needs you", then "New reply",
 * `lib/runSignals.ts`). `null` draws no badge.
 */
export function runBadge(running: number, needsInput: number, unread = 0): RunBadge | null {
  if (needsInput > 0) return { count: needsInput, tone: "warning", label: needsInputLabel(needsInput) };
  if (unread > 0) return { count: unread, tone: "info", label: newReplyLabel(unread) };
  if (running > 0) return { count: running, tone: "success", label: runningLabel(running) };
  return null;
}

/** The badge's text: a count, capped so it fits a 16px circle. */
export function badgeText(n: number): string {
  return n > 9 ? "9+" : String(n);
}

// ── The activity panel (WS-51 S3) ────────────────────────────────────────────
//
// One list across apps, read from the same runs as the badges. A row is a
// server row (`/chat/active-sessions`, already filtered for this member and
// org), or a run this tab streams that the server has not listed yet. No row
// comes from anywhere else, so the panel shows nothing the badges do not count.

/**
 * A row's state. `new_reply` (WS-51 S5) is a chat whose run ended while the
 * member did not look at it. It comes from the member's unread map, never
 * from the server.
 */
export type ActivityState = "running" | "needs_input" | "new_reply";

/** One row of the activity panel. */
export type ActivityRow = {
  threadId: string;
  agentName: string;
  title: string | null;
  startedAt: string | null;
  state: ActivityState;
  /** The run's latest step, plain text, from the server. */
  lastStep: string | null;
  /** When the run ended, epoch ms. Only a `new_reply` row has it. */
  finishedAt?: number | null;
};

/** The part of an unread entry that a row reads (`lib/runSignals.ts`). */
export type UnreadSource = { at: number; agent: string; title: string | null };

/** The part of a server row that the panel reads. */
export type ActivitySource = RunRef & {
  title?: string | null;
  startedAt?: string | null;
  lastStep?: string | null;
};

/**
 * The panel's rows, newest first. A run this tab streams that the server has
 * not listed yet is the newest of all: it started a moment ago, here. A
 * server row with no start time (a parked question) sorts last.
 *
 * `unread` (WS-51 S5) adds a "New reply" row for each chat in the member's
 * unread map that runs no more. A chat that runs again shows its live row
 * only. An unread row sorts by the time its run ended.
 */
export function activityRows(
  server: readonly ActivitySource[],
  localIds: Iterable<string>,
  localAgent: (threadId: string) => string | undefined,
  localTitle: (threadId: string) => string | null | undefined = () => null,
  unread: Readonly<Record<string, UnreadSource>> = {},
): ActivityRow[] {
  const rows: { row: ActivityRow; at: number }[] = [];
  const seen = new Set<string>();
  for (const r of server) {
    if (seen.has(r.threadId)) continue;
    seen.add(r.threadId);
    const named = r.agentName && r.agentName !== "unknown" ? r.agentName : localAgent(r.threadId);
    const t = r.startedAt ? Date.parse(r.startedAt) : NaN;
    rows.push({
      row: {
        threadId: r.threadId,
        agentName: named ?? r.agentName ?? "unknown",
        title: r.title ?? localTitle(r.threadId) ?? null,
        startedAt: r.startedAt ?? null,
        state: r.state === "needs_input" ? "needs_input" : "running",
        lastStep: r.lastStep ?? null,
      },
      at: Number.isFinite(t) ? t : -Infinity,
    });
  }
  for (const id of localIds) {
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push({
      row: {
        threadId: id,
        agentName: localAgent(id) ?? "unknown",
        title: localTitle(id) ?? null,
        startedAt: null,
        state: "running",
        lastStep: null,
      },
      at: Infinity,
    });
  }
  for (const [id, entry] of Object.entries(unread)) {
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push({
      row: {
        threadId: id,
        agentName: entry.agent || "unknown",
        title: entry.title ?? localTitle(id) ?? null,
        startedAt: null,
        state: "new_reply",
        lastStep: null,
        finishedAt: entry.at,
      },
      at: Number.isFinite(entry.at) ? entry.at : -Infinity,
    });
  }
  rows.sort((a, b) => (b.at === a.at ? a.row.threadId.localeCompare(b.row.threadId) : b.at > a.at ? 1 : -1));
  return rows.map((r) => r.row);
}

/** The agent's name for a person: "task-manager" → "Task manager". */
export function agentLabel(agentName: string | null | undefined): string {
  const raw = (agentName ?? "").trim();
  if (!raw || raw === "unknown" || raw === "orchestrator") return "Assistant";
  const words = raw.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** How long a run has run: "under 1 min", "2 min", "1 h 5 min", "3 d". */
export function elapsedLabel(startedAt: string | null | undefined, now: number): string | null {
  if (!startedAt) return null;
  const t = Date.parse(startedAt);
  if (!Number.isFinite(t)) return null;
  const mins = Math.floor(Math.max(0, now - t) / 60_000);
  if (mins < 1) return "under 1 min";
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return mins % 60 ? `${hours} h ${mins % 60} min` : `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

/** The step, cut to one short line. The server caps it too. */
export const STEP_MAX_CHARS = 60;

/** The words of a row whose run ended unread (WS-51 S5). */
export const NEW_REPLY_TEXT = "New reply";

/**
 * A row's status line: "Running · 2 min · Search tasks", "Needs your answer",
 * or "New reply · 5 min ago".
 */
export function runStatusText(
  row: Pick<ActivityRow, "state" | "startedAt" | "lastStep" | "finishedAt">,
  now: number,
): string {
  if (row.state === "needs_input") return "Needs your answer";
  if (row.state === "new_reply") {
    if (typeof row.finishedAt !== "number" || !Number.isFinite(row.finishedAt)) return NEW_REPLY_TEXT;
    const ago = elapsedLabel(new Date(row.finishedAt).toISOString(), now);
    return ago === "under 1 min" ? `${NEW_REPLY_TEXT} · just now` : `${NEW_REPLY_TEXT} · ${ago} ago`;
  }
  const parts = ["Running"];
  const elapsed = elapsedLabel(row.startedAt, now);
  if (elapsed) parts.push(elapsed);
  const step = row.lastStep?.trim();
  if (step) parts.push(step.length > STEP_MAX_CHARS ? `${step.slice(0, STEP_MAX_CHARS - 1)}…` : step);
  return parts.join(" · ");
}

/**
 * The shell job that opens one chat in its app (`lib/shell/doJob.tsx`). The
 * link is `<app>?do=open-chat&fill.session=<thread id>`, so it works from any
 * page, and the app takes the job out of the address once it has opened it.
 */
export const OPEN_CHAT_JOB = "open-chat";

/** What Chat says when a link names a chat that is not open to the member. */
export const CHAT_GONE_NOTICE = "That chat is no longer available.";

/**
 * The apps that open a chat in their own assistant. Each one renders
 * `<ShellJob id={OPEN_CHAT_JOB}>`. Every other agent opens in Chat.
 */
export const CHAT_IN_APP: ReadonlySet<string> = new Set(["/projects", "/tasks", "/email"]);

/**
 * Where a tap on a row goes: the chat in its own app, or in Chat. A run on a
 * pane the member cannot see opens in Chat, like the badge fold.
 */
export function chatLink(
  agentName: string | null | undefined,
  threadId: string,
  visibleHrefs: ReadonlySet<string> | null = null,
  sections: readonly NavSection[] = NAV_SECTIONS,
): string {
  let href = appForAgent(agentName, sections);
  if (visibleHrefs && !visibleHrefs.has(href)) href = CHAT_HREF;
  if (!CHAT_IN_APP.has(href)) href = CHAT_HREF;
  return `${href}?do=${OPEN_CHAT_JOB}&fill.session=${encodeURIComponent(threadId)}`;
}

/** The pane a row belongs to, for its app name and icon. */
export function paneForAgent(
  agentName: string | null | undefined,
  sections: readonly NavSection[] = NAV_SECTIONS,
): { href: string; label: string; icon: string } | null {
  const href = appForAgent(agentName, sections);
  for (const section of sections) {
    for (const pane of section.items) {
      if (pane.href === href) return { href: pane.href, label: pane.label, icon: pane.icon };
    }
  }
  return null;
}
