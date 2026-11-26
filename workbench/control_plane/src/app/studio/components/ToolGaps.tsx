"use client";

/**
 * The Tool gaps section of the Studio library (SBX-1b, task 3984).
 *
 * Spec: `project-docs/specs/agent_sandbox.md` §9.2 and §12.1 decision 10.
 *
 * Data:  GET /api/studio/tool-gaps  (the proxy of the gateway `/studio`
 *        routes, SBX-1a)
 *
 * The section reads only. The gateway holds every check. It answers 404 when
 * the Studio switch or `user_tool_find_enabled` is off, and 403 to a member
 * without `studio:publish`. On a 404, a 401 and a 403 the section draws
 * nothing, so it stays dark. A miss of the network or a fault of the server
 * draws one quiet error line. A 200 draws the counts of 30 days by outcome and
 * by category, and the newest rows.
 *
 * The need text is the words of a member. React draws it as text, never as
 * HTML.
 */

import { EmptyState } from "@/components/EmptyState";
import Badge from "@/components/ui/Badge";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

/** The cache key of the read. One key per org, because the cache binds the identity. */
export const TOOL_GAPS_KEY = "studio/tool-gaps";

export interface ToolGapRow {
  need: string;
  category: string;
  outcome: string;
  created_at: string;
}

export interface ToolGaps {
  days: number;
  counts: { by_outcome: Record<string, number>; by_category: Record<string, number> };
  gaps: ToolGapRow[];
}

export type ToolGapsView =
  | { kind: "off" }
  | { kind: "error"; detail: string }
  | { kind: "ok"; data: ToolGaps };

/** The error line. It says what happened and what to do. */
export const TOOL_GAPS_ERROR = "The tool gaps did not load. Reload the page to try again.";

/** The words of each outcome of `tool_gaps.outcome` (§7.4). */
const OUTCOME_WORDS: Record<string, string> = {
  existing: "Existing tool",
  reuse: "Reused",
  compose: "Composed",
  no_match: "No match",
  unsure: "Unsure",
  declined: "Declined",
  built: "Built",
  build_failed: "Build failed",
};

export function outcomeWord(outcome: string): string {
  return OUTCOME_WORDS[outcome] ?? outcome;
}

/** The view of one answer of the route. A 404, a 401 and a 403 are off. */
export async function loadToolGapsView(): Promise<ToolGapsView> {
  let res: Response;
  try {
    res = await fetch("/api/studio/tool-gaps", { cache: "no-store" });
  } catch {
    return { kind: "error", detail: TOOL_GAPS_ERROR };
  }
  if (res.status === 404 || res.status === 401 || res.status === 403) return { kind: "off" };
  if (!res.ok) return { kind: "error", detail: TOOL_GAPS_ERROR };
  const body = (await res.json().catch(() => null)) as Partial<ToolGaps> | null;
  if (!body || typeof body !== "object") return { kind: "error", detail: TOOL_GAPS_ERROR };
  return {
    kind: "ok",
    data: {
      days: typeof body.days === "number" ? body.days : 30,
      counts: {
        by_outcome: body.counts?.by_outcome ?? {},
        by_category: body.counts?.by_category ?? {},
      },
      gaps: Array.isArray(body.gaps) ? body.gaps : [],
    },
  };
}

/** A date that a reader in India and a reader in the US read as one day. */
function dayOf(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function Counts({ label, counts, word }: {
  label: string;
  counts: Record<string, number>;
  word: (key: string) => string;
}) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{label}</h3>
      <ul className="flex flex-wrap gap-1.5">
        {entries.map(([key, n]) => (
          <li key={key}>
            <Badge size="sm">{word(key)} · {n}</Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The section for one view. It draws nothing for the off view. */
export function ToolGapsSection({ view }: { view: ToolGapsView | null }) {
  if (view === null || view.kind === "off") return null;
  return (
    <section aria-label="Tool gaps" className="flex flex-col">
      <div className="flex items-baseline gap-x-3 px-4 sm:px-6 py-3 border-b border-border">
        <h2 className="text-sm font-bold text-foreground">Tool gaps</h2>
        {view.kind === "ok" && (
          <span className="text-xs text-muted-foreground">Last {view.data.days} days</span>
        )}
      </div>

      {view.kind === "error" && (
        <p role="alert" className="px-4 sm:px-6 py-3 text-xs text-destructive">{view.detail}</p>
      )}

      {view.kind === "ok" && view.data.gaps.length === 0 && (
        <EmptyState
          icon="Search"
          message="No tool gaps yet."
          hint="When the assistant finds no tool for a request, the request shows here."
        />
      )}

      {view.kind === "ok" && view.data.gaps.length > 0 && (
        <>
          <div className="flex flex-col gap-3 px-4 sm:px-6 py-3 border-b border-border">
            <Counts label="By outcome" counts={view.data.counts.by_outcome} word={outcomeWord} />
            <Counts label="By category" counts={view.data.counts.by_category} word={(k) => k} />
          </div>
          <ul className="flex flex-col divide-y divide-border">
            {view.data.gaps.map((gap, i) => (
              <li
                key={`${gap.created_at}-${i}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 sm:px-6 py-3"
              >
                <span className="min-w-0 flex-1 basis-60 break-words text-sm text-foreground">{gap.need}</span>
                <Badge size="sm">{outcomeWord(gap.outcome)}</Badge>
                <span className="text-xs text-muted-foreground">{gap.category}</span>
                <span className="text-xs text-muted-foreground">{dayOf(gap.created_at)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/**
 * The browser remembers that the route answered 200 once. Only then does the
 * panel draw a skeleton on a first read, or an error line on a failed read.
 * A member of an org where the feature is dark never saw a 200, so that
 * member never sees the panel, its skeleton, or an error of a gateway restart.
 * The hint picks what draws while nothing is known. It never holds data.
 */
export const TOOL_GAPS_SEEN = "studio.toolGaps.seen";

export function seenOn(): boolean {
  try {
    return window.localStorage.getItem(TOOL_GAPS_SEEN) === "1";
  } catch {
    return false;
  }
}

function remember(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(TOOL_GAPS_SEEN, "1");
    else window.localStorage.removeItem(TOOL_GAPS_SEEN);
  } catch {
    /* private window or blocked storage: the panel stays quiet */
  }
}

/**
 * What the panel draws, from the read state and the hint. Pure, so the
 * tests drive it with no DOM.
 */
export function panelState(
  loading: boolean,
  view: ToolGapsView | undefined,
  seen: boolean,
): "skeleton" | "hidden" | "view" {
  if (view === undefined) return loading && seen ? "skeleton" : "hidden";
  if (view.kind === "off") return "hidden";
  if (view.kind === "error") return seen ? "view" : "hidden";
  return "view";
}

/** The read: the view, and the hint kept up to date. */
async function readToolGaps(): Promise<ToolGapsView> {
  const view = await loadToolGapsView();
  if (view.kind === "ok") remember(true);
  if (view.kind === "off") remember(false);
  return view;
}

/**
 * The section, with its read through the shared cache (`useCachedResource`).
 * The read never throws: an error is a view, so the cache holds the off view
 * too. The caller gives the panel class.
 */
export default function ToolGapsPanel({ className }: { className: string }) {
  const res = useCachedResource<ToolGapsView>(TOOL_GAPS_KEY, readToolGaps, { ttl: 60_000 });
  const state = panelState(res.loading, res.data, seenOn());
  if (state === "hidden") return null;
  if (state === "skeleton" || res.data === undefined) {
    return (
      <div className={className}>
        <SkeletonRows count={2} className="px-4 sm:px-6 py-3" label="Loading tool gaps" />
      </div>
    );
  }
  return (
    <div className={className}>
      <ToolGapsSection view={res.data} />
    </div>
  );
}
