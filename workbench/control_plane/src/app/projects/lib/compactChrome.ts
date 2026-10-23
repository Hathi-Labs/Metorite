/**
 * Projects · the compact chrome (WS-64 OP-1, `one_projects_app.md` §5).
 *
 * One tool row (search, Filters, View), the active-filter chips, and two
 * sheets that hold every other control. Every rule the chrome needs lives here
 * as a pure function, because the vitest runner has no DOM and a rule written
 * in a `.tsx` would have no fence.
 *
 * The flag is OFF by default. With it off the page draws the old `FilterBar`,
 * unchanged.
 */

import { CATEGORY_LABEL } from "@/lib/statusCategory";

import { AGENT_STRIP, type AgentFocus, type AgentSummary } from "./agentRuns";
import { type Filters } from "./grouping";
import { hasMoreTasks } from "./paging";

/**
 * The flag. ⚠️ Only the LITERAL `process.env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME`
 * is inlined into the browser bundle, so the default reads it literally and a
 * test passes an explicit `env` instead. `publicFlags.test.ts` fences it.
 */
export function compactChromeEnabled(env?: Record<string, string | undefined>): boolean {
  const raw = env
    ? env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME
    : process.env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME;
  return ["1", "true", "on", "yes"].includes(String(raw ?? "").trim().toLowerCase());
}

/** What a chip clears. `filters` is a patch of `Filters`; the two toggles are page state. */
export interface ActiveChip {
  key: string;
  label: string;
  clear:
    | { kind: "filters"; patch: Partial<Filters> }
    | { kind: "waitingOnMe" }
    | { kind: "agentWork" };
}

export interface ChipInput {
  filters: Filters;
  waitingOnMe: boolean;
  agentWork: boolean;
  me: string;
  /** Turns an address into the name a member reads. */
  labelOf?: (address: string) => string;
}

/**
 * One chip per filter that is on. The search text is NOT a chip: the search
 * field in the tool row already shows it, and holds the clear button.
 */
export function activeChips({
  filters,
  waitingOnMe,
  agentWork,
  me,
  labelOf = (address) => address,
}: ChipInput): ActiveChip[] {
  const chips: ActiveChip[] = [];
  if (filters.statusCategory) {
    const names = CATEGORY_LABEL as Record<string, string>;
    chips.push({
      key: "stage",
      label: names[filters.statusCategory] ?? filters.statusCategory,
      clear: { kind: "filters", patch: { statusCategory: "" } },
    });
  }
  if (filters.unassigned) {
    chips.push({
      key: "assignee",
      label: "Unassigned",
      clear: { kind: "filters", patch: { unassigned: false } },
    });
  } else if (filters.assignee.trim()) {
    const who = filters.assignee.trim();
    chips.push({
      key: "assignee",
      label: me && who.toLowerCase() === me.toLowerCase() ? "Me" : labelOf(who),
      clear: { kind: "filters", patch: { assignee: "" } },
    });
  }
  if (filters.overdue) {
    chips.push({ key: "overdue", label: "Overdue", clear: { kind: "filters", patch: { overdue: false } } });
  }
  if (filters.watching) {
    chips.push({ key: "watching", label: "Watching", clear: { kind: "filters", patch: { watching: false } } });
  }
  if (waitingOnMe) {
    chips.push({ key: "waiting", label: "Waiting on me", clear: { kind: "waitingOnMe" } });
  }
  if (agentWork) {
    chips.push({ key: "agent", label: "Agent work", clear: { kind: "agentWork" } });
  }
  if (filters.archived) {
    chips.push({ key: "archived", label: "Archived", clear: { kind: "filters", patch: { archived: false } } });
  }
  for (const tag of filters.tags) {
    chips.push({
      key: `tag:${tag}`,
      label: tag,
      clear: {
        kind: "filters",
        patch: { tags: filters.tags.filter((name) => name !== tag) },
      },
    });
  }
  return chips;
}

/** The button's words: "Filters", or "Filters 2" when two filters are on. */
export function filtersButtonLabel(count: number): string {
  return count > 0 ? `Filters ${count}` : "Filters";
}

/** The agent-strip items worth drawing: a count above zero, or the one that is on. */
export function visibleAgentItems(
  summary: AgentSummary | null | undefined,
  focus: AgentFocus | null
): Array<(typeof AGENT_STRIP)[number] & { count: number }> {
  if (!summary) return [];
  return AGENT_STRIP.map((item) => ({ ...item, count: summary[item.field] ?? 0 })).filter(
    (item) => item.count > 0 || item.focus === focus
  );
}

/** The short word for each count when the strip folds to one chip. */
const FOLDED_WORD: Record<AgentFocus, string> = {
  waiting: "waiting",
  working: "working",
  queued: "queued",
  review: "in review",
  merged: "merged",
};

/**
 * "1 waiting · 2 working" — the phone's one chip. Empty when every count is
 * zero, and then no chip draws. A zero never appears in it.
 */
export function foldedAgentText(summary: AgentSummary | null | undefined): string {
  return visibleAgentItems(summary, null)
    .map((item) => `${item.count} ${FOLDED_WORD[item.focus]}`)
    .join(" · ");
}

/** "100 of 219" for the tool row, or null when the list is whole. */
export function loadedOfTotal(loaded: number, total: number | null): string | null {
  return hasMoreTasks(loaded, total) ? `${loaded} of ${total}` : null;
}

export interface StatusTab {
  key: string;
  label: string;
  count: number;
}

/** The tab that shows: the chosen one if it still exists, else the first. */
export function activeTabKey(tabs: readonly StatusTab[], chosen: string | null): string | null {
  if (tabs.length === 0) return null;
  return tabs.some((tab) => tab.key === chosen) ? chosen : tabs[0].key;
}

/** A swipe: the next tab (dir 1) or the previous one (dir -1), stopping at the ends. */
export function stepTab(
  tabs: readonly StatusTab[],
  current: string | null,
  dir: 1 | -1
): string | null {
  const here = activeTabKey(tabs, current);
  if (here === null) return null;
  const index = tabs.findIndex((tab) => tab.key === here);
  return tabs[Math.min(tabs.length - 1, Math.max(0, index + dir))].key;
}

/** A touch swipe that is long and flat enough to count as a page swipe. */
export function swipeDirection(dx: number, dy: number): 1 | -1 | null {
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  return dx < 0 ? 1 : -1;
}
