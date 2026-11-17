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
 * reply ends, not while it streams. Fence: `DecisionChips.test.ts`.
 */
import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CHIP_WORDS, fetchDecisions, undoDecision, type Decision } from "@/lib/chatDecisions";

export interface DecisionChipsViewProps {
  items: readonly Decision[];
  busyId: string | null;
  failedId: string | null;
  onUndo: (id: string) => void;
}

/** The chips, with no hook, so a test calls it as a function. */
export function DecisionChipsView({ items, busyId, failedId, onUndo }: DecisionChipsViewProps) {
  if (items.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs" data-testid="decision-chips">
      <span className="text-muted-foreground">{CHIP_WORDS.lead}</span>
      {items.map((d) => (
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
            disabled={busyId === d.id}
            onClick={() => onUndo(d.id)}
          >
            {CHIP_WORDS.undo}
          </Button>
          {failedId === d.id && (
            <span role="status" className="pr-1.5 text-destructive">{CHIP_WORDS.failed}</span>
          )}
        </span>
      ))}
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
  }, [sessionId]);

  const onUndo = useCallback(
    async (id: string) => {
      setBusyId(id);
      setFailedId(null);
      const result = await undoDecision(sessionId, id);
      setBusyId(null);
      // "gone" is as good as "done": the decision is not kept any more.
      if (result === "failed") setFailedId(id);
      else setItems((cur) => cur.filter((d) => d.id !== id));
    },
    [sessionId],
  );

  if (!enabled) return null;
  return <DecisionChipsView items={items} busyId={busyId} failedId={failedId} onUndo={onUndo} />;
}
