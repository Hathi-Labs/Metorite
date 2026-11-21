"use client";

/**
 * DecisionChips — one chip for each decision that this chat keeps for the
 * member, each with an Undo (HRD-12b, `chat_failure_modes.md` §6).
 *
 * A decision is a sentence that the member typed ("TEDx is done."). The
 * assistant follows it for the rest of the chat (HRD-12a). The chip shows it
 * and lets the member take it back at once. The next reply no longer follows
 * it, because the orchestrator reads the list again at each turn.
 *
 * It draws nothing when the flag is off (the server says `enabled: false`),
 * when the chat is not the member's own assistant chat, when nothing is kept,
 * and when the list cannot be read. It reads when the chat settles: when a
 * reply ends, not while it streams. More than three chips collapse behind a
 * "Show N more" button. One Undo runs at a time: every Undo is disabled while
 * one is in flight, and focus then moves to the next Undo. Fence:
 * `DecisionChips.test.ts` and `DecisionChips.fence.test.ts`.
 */
import { useCallback, useEffect, useRef, useState, type Ref } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CHIP_WORDS, fetchDecisions, undoDecision, type Decision } from "@/lib/chatDecisions";

/** Chips drawn before "Show N more". The server lists at most 10. */
export const COLLAPSED_LIMIT = 3;

/** Where focus goes after an Undo. */
export type FocusTarget = { kind: "undo"; id: string } | { kind: "lead" };

/**
 * The element that takes focus after an Undo: the chip's own Undo when the
 * Undo failed (the chip stays), else the next chip's Undo, else the previous
 * chip's, else the lead text of the list. `ids` is the list before the Undo.
 */
export function focusTargetAfterUndo(ids: readonly string[], id: string, removed: boolean): FocusTarget {
  if (!removed) return { kind: "undo", id };
  const at = ids.indexOf(id);
  const rest = ids.filter((x) => x !== id);
  if (rest.length === 0) return { kind: "lead" };
  return { kind: "undo", id: rest[Math.min(Math.max(at, 0), rest.length - 1)] };
}

export interface DecisionChipsViewProps {
  items: readonly Decision[];
  busyId: string | null;
  failedId: string | null;
  onUndo: (id: string) => void;
  /** True while a reply streams. A turn that keeps or drops an item would write
   *  the list again and bring an Undo back, so no Undo is offered then. */
  locked?: boolean;
  /** True shows every chip. False shows the first {@link COLLAPSED_LIMIT}. */
  expanded?: boolean;
  onToggle?: () => void;
  rootRef?: Ref<HTMLDivElement>;
}

/** The chips, with no hook, so a test calls it as a function. */
export function DecisionChipsView({
  items,
  busyId,
  failedId,
  onUndo,
  locked = false,
  expanded = false,
  onToggle,
  rootRef,
}: DecisionChipsViewProps) {
  if (items.length === 0) return null;
  const hidden = Math.max(0, items.length - COLLAPSED_LIMIT);
  const shown = expanded || hidden === 0 ? items : items.slice(0, COLLAPSED_LIMIT);
  return (
    <div ref={rootRef} className="mb-2 flex flex-wrap items-center gap-1.5 text-xs" data-testid="decision-chips">
      <span className="text-muted-foreground outline-none" tabIndex={-1} data-decision-lead="">
        {CHIP_WORDS.lead}
      </span>
      {shown.map((d) => (
        <span
          key={d.id}
          className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-secondary/30 py-0.5 pl-2.5 pr-1"
        >
          <Icon name="Check" size={12} className="shrink-0 text-success" />
          <span className="min-w-0 truncate text-foreground" title={d.text}>{d.text}</span>
          <Button
            variant="ghost"
            size="sm"
            aria-label={CHIP_WORDS.undoLabel(d.text)}
            data-undo-id={d.id}
            disabled={locked || busyId !== null}
            onClick={() => onUndo(d.id)}
          >
            {CHIP_WORDS.undo}
          </Button>
          {failedId === d.id && (
            <span role="status" className="pr-1.5 text-destructive">{CHIP_WORDS.failed}</span>
          )}
        </span>
      ))}
      {hidden > 0 && onToggle && (
        <Button variant="ghost" size="sm" aria-expanded={expanded} onClick={onToggle}>
          {expanded ? CHIP_WORDS.showFewer : CHIP_WORDS.showMore(hidden)}
        </Button>
      )}
    </div>
  );
}

export interface DecisionChipsProps {
  sessionId: string;
  /** True in the member's own assistant chat only (not a shared room). */
  enabled: boolean;
  /** Changes when the chat settles. `null` while a reply streams: no read then. */
  settledKey: string | number | null;
}

export default function DecisionChips({ sessionId, enabled, settledKey }: DecisionChipsProps) {
  const [items, setItems] = useState<Decision[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const focusAfter = useRef<FocusTarget | null>(null);

  // After an Undo the pressed button is gone or disabled, so focus would drop
  // to the page. Put it on the next Undo, or on the lead text of the list.
  useEffect(() => {
    const target = focusAfter.current;
    if (!target || busyId !== null) return;
    focusAfter.current = null;
    const root = rootRef.current;
    if (!root) return;
    const el =
      target.kind === "undo"
        ? root.querySelector<HTMLElement>(`[data-undo-id="${target.id}"]`)
        : root.querySelector<HTMLElement>("[data-decision-lead]");
    el?.focus();
  }, [items, busyId, failedId]);

  useEffect(() => {
    if (!enabled || !sessionId || settledKey === null) return;
    let live = true;
    void fetchDecisions(sessionId).then((list) => {
      if (live) setItems(list?.enabled ? list.items : []);
    });
    return () => {
      live = false;
    };
  }, [enabled, sessionId, settledKey]);

  // Another chat starts with no chip of the last one.
  useEffect(() => {
    setItems([]);
    setBusyId(null);
    setFailedId(null);
    setExpanded(false);
  }, [sessionId]);

  const onUndo = useCallback(
    async (id: string) => {
      if (busyId !== null) return;
      setBusyId(id);
      setFailedId(null);
      const result = await undoDecision(sessionId, id);
      // "gone" is as good as "done": the decision is not kept any more.
      focusAfter.current = focusTargetAfterUndo(items.map((d) => d.id), id, result !== "failed");
      if (result === "failed") setFailedId(id);
      else setItems((cur) => cur.filter((d) => d.id !== id));
      setBusyId(null);
    },
    [sessionId, busyId, items],
  );

  if (!enabled) return null;
  return (
    <DecisionChipsView
      items={items}
      busyId={busyId}
      failedId={failedId}
      onUndo={onUndo}
      locked={settledKey === null}
      expanded={expanded}
      onToggle={() => setExpanded((v) => !v)}
      rootRef={rootRef}
    />
  );
}
