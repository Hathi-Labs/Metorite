"use client";

/**
 * "AI waiting on you" on My Day (owner, 2026-11-01: "fold needs you into my
 * day ... so that there is only one surface"). The assistant runs that wait
 * on the member's answer or confirmation, from the one run store. A row opens
 * that chat. Projects' agent questions come through the feed itself
 * (`agent_question`, `routes/shell/needs.py`).
 *
 * `useAiWaiting` reads the rows. `AiWaitingGroup` draws them as a group of
 * the feed: a label with its count, then one surface. The feed's column holds
 * the group. The cards layout draws `AiWaitingCard`. Neither draws anything
 * when no run waits. The sidebar's My Day row counts these runs too.
 */

import { useEffect, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { useActivityRows } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { wantLiveSteps } from "@/lib/liveRuns";
import type { ActivityRow } from "@/lib/runActivity";
import { ActivityList, useHeldHrefs } from "./ActivityControl";
import { openAssistant } from "./assistant";
import { shellChatOn } from "./chatFlag";

/** How often the relative times move. */
const TICK_MS = 30_000;

/** The runs that wait on the member's answer or confirmation. */
export function useAiWaiting(): ActivityRow[] {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  return useActivityRows(workspace).filter((r) => r.state === "needs_input");
}

/** The rows as a group of the feed. Nothing when no run waits. */
export function AiWaitingGroup({ rows, className = "" }: { rows: readonly ActivityRow[]; className?: string }) {
  const heldHrefs = useHeldHrefs();
  const [chatOn] = useState(() => shellChatOn());
  const [now, setNow] = useState(() => Date.now());
  const shown = rows.length > 0;

  // The run steps and the clock, only while the group shows.
  useEffect(() => {
    if (!shown) return;
    const release = wantLiveSteps();
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => {
      release();
      clearInterval(id);
    };
  }, [shown]);

  if (!shown) return null;
  return (
    <div role="group" aria-labelledby="feed-group-ai-waiting" data-testid="ai-waiting" className={className}>
      <div className="flex items-center gap-1.5 px-1 pb-2 text-xs font-medium text-warning">
        <Icon name="Sparkles" size={12} />
        <span id="feed-group-ai-waiting">AI waiting on you</span>
        <span className="tabular-nums opacity-70">{rows.length}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card px-1 glass:rounded-[16px] glass:border-transparent glass:glass-raised">
        <ActivityList
          rows={rows}
          now={now}
          visibleHrefs={heldHrefs}
          onOpenChat={chatOn ? (row) => openAssistant({ sessionId: row.threadId }) : undefined}
        />
      </div>
    </div>
  );
}

/** The group on its own, for My Day's cards layout. */
export default function AiWaitingCard({ className = "" }: { className?: string }) {
  const rows = useAiWaiting();
  return <AiWaitingGroup rows={rows} className={className} />;
}
