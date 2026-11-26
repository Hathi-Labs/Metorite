"use client";

/**
 * The Agents view · the card of one session (WS-MCP AMR-1).
 *
 * It is the board's task card with the agent edge of `agentCardLook`
 * (`data-agent-edge`, drawn by `globals.css`): the spectrum travels while the
 * session works, a still edge waits, a warning edge needs a person, and a slow
 * glow is idle. The Graph, the Board, the List and the phone draw this one card.
 * Every name and line is untrusted text, drawn as text.
 */
import Icon from "@/components/Icon";
import { relativeTime } from "@/lib/taskCard";

import {
  type LensCounts,
  type LensSession,
  type LensState,
  countWords,
  lensChip,
  lensEdge,
  lensIcon,
  lensWord,
  machineWord,
  sessionLine,
} from "../lib/agentLenses";

export const STATE_PILL = "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-px text-[11px] font-medium";

export function StatePill({ state }: { state: LensState }) {
  return (
    <span data-agent-lens-state={state} className={`${STATE_PILL} ${lensChip(state)}`}>
      <Icon name={lensIcon(state)} size={11} aria-hidden />
      {lensWord(state)}
    </span>
  );
}

/** Enter and Space press a `role="button"` element, as they press a button. */
export function pressKeys(act: () => void) {
  return (event: React.KeyboardEvent) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      act();
    }
  };
}

/** The words that a screen reader says for a session card. */
export function sessionSpoken(s: LensSession, nowMs: number): string {
  return `${s.name}. ${lensWord(s.state)}. ${sessionLine(s, nowMs)}. Machine ${machineWord(s)}.`;
}

/** The raised face of a card inside an agent edge. */
export const CARD_FACE =
  "relative flex h-full w-full min-w-0 cursor-pointer flex-col justify-center gap-0.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-left text-foreground shadow-sm hover:shadow-md";

export function SessionCard({
  session,
  nowMs,
  onOpen,
  selected = false,
}: {
  session: LensSession;
  nowMs: number;
  onOpen: () => void;
  selected?: boolean;
}) {
  const loud = session.state === "you" || session.state === "waiting";
  const age = relativeTime(session.lastAt, nowMs);
  return (
    <div
      data-agent-edge={lensEdge(session.state) ?? undefined}
      data-agent-session={session.state}
      className={`h-full rounded-lg ${selected ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : ""}`}
    >
      <div
        role="button"
        tabIndex={0}
        aria-label={sessionSpoken(session, nowMs)}
        aria-pressed={selected}
        onClick={onOpen}
        onKeyDown={pressKeys(onOpen)}
        className={CARD_FACE}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          {loud ? (
            <StatePill state={session.state} />
          ) : (
            <Icon name={lensIcon(session.state)} size={13} className="shrink-0 text-muted-foreground" aria-hidden />
          )}
          <span className="min-w-0 truncate text-sm font-semibold">{session.name}</span>
          {age ? <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{age}</span> : null}
        </span>
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{sessionLine(session, nowMs)}</span>
          {session.host ? (
            <span className="max-w-[40%] shrink-0 truncate rounded bg-secondary px-1 text-[11px] text-muted-foreground">
              {session.host}
            </span>
          ) : null}
        </span>
      </div>
    </div>
  );
}

/** "1 needs you · 2 working", with the need in the warning tone. */
export function CountLine({ counts }: { counts: LensCounts }) {
  return (
    <span className="flex min-w-0 gap-2 truncate text-[11px] text-muted-foreground">
      {countWords(counts).map((part) => (
        <span key={part.state} className={part.state === "you" ? "text-warning" : undefined}>
          {part.text}
        </span>
      ))}
    </span>
  );
}

/** The one line that stands for the folded sessions of a group. */
export function FoldRow({ word, onShow }: { word: string; onShow: () => void }) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Show ${word}`}
      onClick={onShow}
      onKeyDown={pressKeys(onShow)}
      data-agent-fold=""
      className="flex h-full min-h-10 w-full cursor-pointer items-center justify-between gap-2 rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground hover:bg-secondary"
    >
      <span className="truncate">{word}</span>
      <span className="shrink-0 font-medium text-primary">Show</span>
    </div>
  );
}
