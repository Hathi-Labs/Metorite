/**
 * The Agents view · the squares of a group and the bar of its sub-agents
 * (WS-MCP AMR-4, §3.12.9). One square is one session, the most urgent first.
 * Each fill is a token of `AGENT_MARKS`, and each square says its session and
 * its state, so the colour is never the only signal. Names are untrusted text.
 */
import Button from "@/components/ui/Button";
import { relativeTime } from "@/lib/taskCard";

import {
  type LensCounts,
  type LensSession,
  type SubCounts,
  lensMark,
  lensWord,
  sessionLine,
  subWords,
} from "../lib/agentLenses";
import { SQUARES_MAX } from "../lib/agentScale";

export const NO_MATCH_COPY = "No session matches these filters.";
export const NO_ORCHESTRATOR = "No orchestrator";

/** What the tip of a square says. */
export function squareTip(s: LensSession, nowMs: number): string {
  const when = relativeTime(s.lastAt, nowMs);
  return [`${s.name} · ${lensWord(s.state)}${when ? ` · ${when}` : ""}`, sessionLine(s, nowMs), subWords(s.subs)]
    .filter(Boolean)
    .join("\n");
}

export function Square({ state }: { state: LensSession["state"] }) {
  return <i aria-hidden className={`block size-2.5 shrink-0 rounded-xs ${lensMark(state)}`} />;
}

/**
 * The squares of a group. With `onPick` a square opens its session. The
 * squares take no tab stop: the group that holds them is the keyboard way in,
 * and the find box and the List reach each session.
 */
export function Squares({
  sessions,
  nowMs,
  onPick,
  max = SQUARES_MAX,
}: {
  sessions: readonly LensSession[];
  nowMs: number;
  onPick?: (session: LensSession) => void;
  max?: number;
}) {
  const rest = sessions.length - Math.min(sessions.length, max);
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-0.5" data-agent-squares="">
      {sessions.slice(0, max).map((s) => (
        <span
          key={s.id}
          data-agent-square={s.state}
          title={squareTip(s, nowMs)}
          {...(onPick
            ? {
                role: "button",
                tabIndex: -1,
                "aria-label": `${s.name}. ${lensWord(s.state)}.`,
                onClick: (event: React.MouseEvent) => {
                  event.stopPropagation();
                  onPick(s);
                },
                className: "cursor-pointer rounded-xs hover:ring-2 hover:ring-foreground",
              }
            : {})}
        >
          <Square state={s.state} />
        </span>
      ))}
      {rest > 0 ? <span className="text-[11px] text-muted-foreground">+{rest}</span> : null}
    </span>
  );
}

/** The parts of the sub-agent bar, in the order of attention. */
export function barParts(subs: SubCounts): Array<{ state: "failed" | "working" | "idle" | "done"; n: number }> {
  const other = Math.max(0, subs.total - subs.failed - subs.working - subs.done);
  return (
    [
      { state: "failed", n: subs.failed },
      { state: "working", n: subs.working },
      { state: "idle", n: other },
      { state: "done", n: subs.done },
    ] as const
  ).filter((p) => p.n > 0);
}

export function SubBar({ subs }: { subs: SubCounts }) {
  if (subs.total === 0) return null;
  const parts = barParts(subs);
  return (
    <span className="flex flex-col gap-1" data-agent-sub-bar="">
      <span className="flex justify-between gap-2 text-[11px] text-muted-foreground">
        <span className="whitespace-nowrap">
          {subs.total} {subs.total === 1 ? "subagent" : "subagents"}
        </span>
        <span className="truncate">
          {[subs.working > 0 ? `${subs.working} working` : "", subs.failed > 0 ? `${subs.failed} failed` : ""]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
      <span
        role="img"
        aria-label={parts.map((p) => `${p.n} ${lensWord(p.state).toLowerCase()}`).join(", ")}
        className="flex h-1.5 overflow-hidden rounded-full bg-secondary"
      >
        {parts.map((p) => (
          <span key={p.state} className={`block h-full ${lensMark(p.state)}`} style={{ width: `${(100 * p.n) / subs.total}%` }} />
        ))}
      </span>
    </span>
  );
}

/** "2 need you · 1 failed · 13 working · 11 waiting · 22 idle or done". */
export function TileCounts({ counts }: { counts: LensCounts }) {
  const rest = counts.idle + counts.done;
  const parts: Array<[string, string]> = [];
  if (counts.you > 0) parts.push([counts.you === 1 ? "1 needs you" : `${counts.you} need you`, "text-warning"]);
  if (counts.failed > 0) parts.push([`${counts.failed} failed`, "text-destructive"]);
  if (counts.working > 0) parts.push([`${counts.working} working`, ""]);
  if (counts.waiting > 0) parts.push([`${counts.waiting} waiting`, ""]);
  if (rest > 0) parts.push([`${rest} idle or done`, ""]);
  return (
    <span className="flex min-w-0 flex-wrap gap-x-2 text-[11px] text-muted-foreground">
      {parts.map(([text, tone]) => (
        <span key={text} className={`whitespace-nowrap ${tone}`}>
          {text}
        </span>
      ))}
    </span>
  );
}

/** One bucket of folded sessions: its squares, its words and "Show". */
export function BucketRow({
  word,
  sessions,
  nowMs,
  onOpen,
  onPick,
  max = 24,
}: {
  word: string;
  sessions: readonly LensSession[];
  nowMs: number;
  onOpen: () => void;
  onPick?: (session: LensSession) => void;
  max?: number;
}) {
  return (
    <div
      onClick={onOpen}
      data-agent-bucket=""
      className="flex h-full min-h-10 w-full cursor-pointer items-center gap-2 overflow-hidden rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground hover:bg-secondary"
    >
      <span className="min-w-0 flex-1 overflow-hidden">
        <Squares sessions={sessions} nowMs={nowMs} max={max} onPick={onPick} />
      </span>
      <Button
        variant="text"
        size="none"
        aria-label={`Show ${word}`}
        onClick={(event) => {
          event.stopPropagation();
          onOpen();
        }}
        layout="shrink-0 whitespace-nowrap"
      >
        {word} · Show
      </Button>
    </div>
  );
}
