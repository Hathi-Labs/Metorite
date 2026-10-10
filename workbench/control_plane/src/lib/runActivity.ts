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

/** What a nav entry wears for its runs: a count, a tone and a spoken name. */
export type RunBadge = { count: number; tone: "success" | "warning"; label: string };

/**
 * The ONE rule for a pane's run badge (WS-51 S2). A run that waits on the
 * member wins: the badge is amber (`warning`) and counts those runs. Else it
 * is green (`success`) and counts every run. `null` draws no badge.
 */
export function runBadge(running: number, needsInput: number): RunBadge | null {
  if (needsInput > 0) return { count: needsInput, tone: "warning", label: needsInputLabel(needsInput) };
  if (running > 0) return { count: running, tone: "success", label: runningLabel(running) };
  return null;
}

/** The badge's text: a count, capped so it fits a 16px circle. */
export function badgeText(n: number): string {
  return n > 9 ? "9+" : String(n);
}
