"use client";

/**
 * Projects · the board saying out loud that it holds only part of the work.
 *
 * `GET /projects/tasks` answers at most one page and reports the true `total`.
 * Before this bar the board drew the page and said nothing, so a project with
 * 150 tasks looked exactly like a project with 100 — same lane counts, same
 * "+ Add" at the end of the list, no scrollbar hint, nothing.
 *
 * ⚠️ **This is the third copy of one house rule, not a new idea.** The calendar
 * and the timeline already take a `truncated` flag and print a sentence.
 * `export/tasks.csv` refuses outright rather than write a partial file. H-76
 * made the Customer Console say "100 of 563". The rule those share:
 * **the truncation is never silent.**
 *
 * The difference here, and the reason this one carries a button: the board CAN
 * go and get the rest. The calendar's cap is a window it cannot widen, so it
 * tells you to narrow the filters. This one offers the next page instead.
 *
 * Every decision lives in `lib/paging.ts` — `vitest` runs in `environment:
 * "node"` and never collects a `.tsx`, so a rule written here would have no
 * fence (D-PM-21).
 */

import Button from "@/components/ui/Button";

import { nextBatchSize, truncationNote } from "../lib/paging";

interface Props {
  /** Rows on screen right now. */
  loaded: number;
  /** What the server says exists under the same filters. `null` until read. */
  total: number | null;
  busy: boolean;
  onLoadMore(): void;
}

export function MoreTasksBar({ loaded, total, busy, onLoadMore }: Props) {
  const note = truncationNote(loaded, total);
  // ⚠️ Renders NOTHING on a whole board, which is nearly every board. A bar
  // that always occupied a row would cost every project a line of screen to
  // say "nothing is wrong".
  if (!note) return null;

  const batch = nextBatchSize(loaded, total);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5 text-xs">
      {/* Not `text-destructive`: nothing is broken and nothing is lost. The
          calendar uses the alarming colour because its cap is a dead end. Here
          the rest of the work is one press away. */}
      <span className="text-muted-foreground">{note}</span>
      <Button
        variant="secondary"
        size="sm"
        icon="ChevronDown"
        loading={busy}
        onClick={onLoadMore}
      >
        {`Load ${batch} more`}
      </Button>
      <span className="text-muted-foreground">
        Lane counts and grouping describe what is loaded.
      </span>
    </div>
  );
}
