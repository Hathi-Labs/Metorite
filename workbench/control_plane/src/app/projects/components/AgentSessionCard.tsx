"use client";

/**
 * The Agents view · the card of one session (WS-MCP AMR-1).
 *
 * AMR-4: a session with sub-agents says how many, on its second line, in the
 * place of the machine. In the graph that count is a button that opens them.
 *
 * It is the board's task card with the agent edge of `agentCardLook`
 * (`data-agent-edge`, drawn by `globals.css`): the spectrum travels while the
 * session works, a still edge waits, a warning edge needs a person, and a slow
 * glow is idle. The Graph, the Board, the List and the phone draw this one card.
 * Every name and line is untrusted text, drawn as text.
 */
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
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
  subParts,
  subWords,
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
  const subs = subWords(s.subs);
  return `${s.name}. ${lensWord(s.state)}. ${sessionLine(s, nowMs)}. Machine ${machineWord(s)}.${subs ? ` ${subs}.` : ""}`;
}

/** The count of the sub-agents, with the failed ones in the destructive ink. */
export function SubCount({ session }: { session: Pick<LensSession, "subs"> }) {
  const parts = subParts(session.subs);
  return (
    <>
      {[parts.total, parts.working].filter(Boolean).join(" · ")}
      {parts.failed ? (
        <>
          {" · "}
          <span className="text-destructive">{parts.failed}</span>
        </>
      ) : null}
    </>
  );
}

const CHIP = "max-w-[65%] shrink-0 truncate rounded bg-secondary px-1 text-[11px] font-normal text-muted-foreground";

/** The raised face of a card inside an agent edge. */
export const CARD_FACE =
  "relative flex h-full w-full min-w-0 cursor-pointer flex-col justify-center gap-0.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-left text-foreground shadow-sm hover:shadow-md";

export function SessionCard({
  session,
  nowMs,
  onOpen,
  selected = false,
  subsOpen = false,
  onToggleSubs,
  namesOnly = false,
}: {
  session: LensSession;
  nowMs: number;
  onOpen: () => void;
  selected?: boolean;
  /** AMR-4: its sub-agent rows show beside it. */
  subsOpen?: boolean;
  /** With it, the sub-agent count opens and closes the rows. */
  onToggleSubs?: () => void;
  /** AMR-4: the graph under 60 % zoom shows the name only. */
  namesOnly?: boolean;
}) {
  const loud = session.state === "you" || session.state === "waiting" || session.state === "failed";
  const age = relativeTime(session.lastAt, nowMs);
  const subs = session.subs.total > 0;
  const toggle = subs && !namesOnly ? onToggleSubs : undefined;
  return (
    <div
      data-agent-edge={lensEdge(session.state) ?? undefined}
      data-agent-session={session.state}
      className={`relative h-full rounded-lg ${selected ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : ""}`}
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
        {namesOnly ? (
          <span className="flex min-w-0 items-center gap-2">
            <Icon name={lensIcon(session.state)} size={18} className="shrink-0 text-muted-foreground" aria-hidden />
            <span className="min-w-0 truncate text-xl font-semibold">{session.name}</span>
          </span>
        ) : (
          <>
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
              {toggle ? (
                // Holds the room of the button, which sits above the card.
                <span aria-hidden className={`${CHIP} invisible pl-6`}>
                  <SubCount session={session} />
                </span>
              ) : subs ? (
                <span className={CHIP} data-agent-sub-count="">
                  <SubCount session={session} />
                </span>
              ) : session.host ? (
                <span className="max-w-[40%] shrink-0 truncate rounded bg-secondary px-1 text-[11px] text-muted-foreground">
                  {session.host}
                </span>
              ) : null}
            </span>
          </>
        )}
      </div>
      {toggle ? (
        <Button
          variant="ghost"
          size="none"
          selected={subsOpen}
          aria-expanded={subsOpen}
          aria-label={`${subsOpen ? "Hide" : "Show"} ${subWords(session.subs)} of ${session.name}`}
          data-agent-sub-toggle={session.id}
          onClick={toggle}
          layout={`!absolute bottom-2 right-2.5 z-10 flex ${CHIP} items-center gap-0.5`}
        >
          <Icon name={subsOpen ? "ChevronDown" : "ChevronRight"} size={12} aria-hidden />
          <SubCount session={session} />
        </Button>
      ) : null}
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
