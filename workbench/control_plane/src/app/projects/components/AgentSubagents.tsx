"use client";

/**
 * The Agents view · every sub-agent of one session, grouped by state, with the
 * done ones folded (WS-MCP AMR-4, §3.12.9). The dock and the phone sheet draw
 * it. A list past `WINDOW_ABOVE` rows draws only the rows on screen. Names and
 * models are untrusted text.
 */
import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

import { type LensState, type SubCounts, type SubRow, LENS_STATES, lensIcon, lensWord, subWords } from "../lib/agentLenses";
import { childStartedWhen } from "../lib/agentMap";
import { WINDOW_ABOVE } from "../lib/agentScale";
import { useWindowedList } from "../lib/windowedList";
import { Square } from "./AgentSquares";

export const SUBAGENTS_LOADING = "Loading the subagents";
export const SUBAGENTS_ERROR = "The subagents did not load.";
/** Done sub-agents fold when there are more than this. */
export const DONE_FOLD_ABOVE = 3;

type Line = { kind: "head"; state: LensState; n: number; folded: boolean } | { kind: "row"; row: SubRow };

/** The headings and the rows. A folded Done group keeps its heading only. */
export function subLines(rows: readonly SubRow[], doneOpen: boolean): Line[] {
  return LENS_STATES.flatMap((state): Line[] => {
    const list = rows.filter((r) => r.state === state);
    if (list.length === 0) return [];
    const folded = state === "done" && !doneOpen && list.length > DONE_FOLD_ABOVE;
    return [
      { kind: "head", state, n: list.length, folded },
      ...(folded ? [] : list.map((row): Line => ({ kind: "row", row }))),
    ];
  });
}

/** The row heights, in rem. The window sums them at the applied root size. */
const HEIGHT_REM = { head: 1.75, row: 2.5 } as const;

function SubLineView({ line, nowMs, onOpenDone }: { line: Line; nowMs: number; onOpenDone: () => void }) {
  const style = { height: `${HEIGHT_REM[line.kind]}rem` };
  if (line.kind === "head") {
    return (
      <li style={style} className="flex items-center gap-1.5 text-xs text-muted-foreground" data-agent-sub-group={line.state}>
        {line.folded ? (
          <Button variant="text" size="sm" icon={lensIcon(line.state)} aria-expanded={false} onClick={onOpenDone}>
            {line.n} done · Show
          </Button>
        ) : (
          <>
            <Icon name={lensIcon(line.state)} size={12} aria-hidden />
            {lensWord(line.state)} · {line.n}
          </>
        )}
      </li>
    );
  }
  const { row } = line;
  const when = childStartedWhen(row.age, nowMs);
  return (
    <li style={style} className="flex min-w-0 items-center gap-2 border-b border-border" data-agent-detail-child={row.state}>
      <Square state={row.state} />
      <span className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-medium text-foreground">{row.name}</span>
        <span className="truncate text-[11px] text-muted-foreground">{row.model ?? lensWord(row.state)}</span>
      </span>
      <span className="shrink-0 text-[11px] text-muted-foreground">{when}</span>
    </li>
  );
}

function Rows({ lines, nowMs, onOpenDone }: { lines: Line[]; nowMs: number; onOpenDone: () => void }) {
  const heights = useMemo(() => lines.map((l) => HEIGHT_REM[l.kind]), [lines]);
  const { attach, start, end, before, after } = useWindowedList(heights);
  if (lines.length <= WINDOW_ABOVE) {
    return (
      <ul>
        {lines.map((line) => (
          <SubLineView key={line.kind === "row" ? line.row.id : line.state} line={line} nowMs={nowMs} onOpenDone={onOpenDone} />
        ))}
      </ul>
    );
  }
  return (
    <div ref={attach} className="max-h-96 overflow-y-auto" data-agent-sub-window="">
      <ul style={{ paddingTop: `${before}rem`, paddingBottom: `${after}rem` }}>
        {lines.slice(start, end).map((line) => (
          <SubLineView key={line.kind === "row" ? line.row.id : line.state} line={line} nowMs={nowMs} onOpenDone={onOpenDone} />
        ))}
      </ul>
    </div>
  );
}

export function AgentSubagents({
  counts,
  rows,
  failed,
  onRetry,
  nowMs,
}: {
  counts: SubCounts;
  /** `null` while the rows load, or after a failed read. */
  rows: readonly SubRow[] | null;
  failed?: boolean;
  onRetry?: () => void;
  nowMs: number;
}) {
  const [doneOpen, setDoneOpen] = useState(false);
  const lines = useMemo(() => subLines(rows ?? [], doneOpen), [rows, doneOpen]);
  const total = Math.max(counts.total, rows?.length ?? 0);
  if (total === 0) return null;
  return (
    <section aria-label="Subagents" className="flex flex-col gap-1" data-agent-subagents="">
      <h4 className="text-[11px] font-medium text-muted-foreground">{subWords({ ...counts, total })}</h4>
      {rows ? (
        <>
          <Rows lines={lines} nowMs={nowMs} onOpenDone={() => setDoneOpen(true)} />
          {total > rows.length ? (
            <p className="text-[11px] text-muted-foreground">The list shows the first {rows.length}.</p>
          ) : null}
        </>
      ) : failed ? (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-xs text-foreground">
            {SUBAGENTS_ERROR}
          </p>
          {onRetry ? (
            <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : (
        <div aria-label={SUBAGENTS_LOADING} role="status" className="flex flex-col gap-1">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      )}
    </section>
  );
}

export default AgentSubagents;
