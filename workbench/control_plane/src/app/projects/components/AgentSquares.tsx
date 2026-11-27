/**
 * The Agents view · the squares of a group and the bar of its sub-agents
 * (WS-MCP AMR-4, §3.12.9). One square is one session, the most urgent first.
 * Each fill is a token of `AGENT_MARKS`, and each square says its session and
 * its state, so the colour is never the only signal. Names are untrusted text.
 */
import { relativeTime } from "@/lib/taskCard";

import {
  type LensSession,
  type SubCounts,
  LENS_STATES,
  lensMark,
  lensWord,
  sessionLine,
  subWords,
} from "../lib/agentLenses";
import { SQUARES_MAX } from "../lib/agentScale";

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

export function SquareLegend() {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      {LENS_STATES.map((state) => (
        <span key={state} className="inline-flex items-center gap-1">
          <Square state={state} />
          {lensWord(state)}
        </span>
      ))}
      <span>One square is one session, the most urgent first.</span>
    </p>
  );
}
