"use client";

/**
 * "AI waiting on you" on My Day (owner, 2026-11-01: "fold needs you into my
 * day ... so that there is only one surface"). The assistant runs that wait
 * on the member's answer or confirmation, from the one run store. A row opens
 * that chat. The card draws nothing when no run waits.
 *
 * It sits above both My Day layouts, so the feed and the cards show it
 * alike. The sidebar's My Day row counts these runs with the feed's rows.
 */

import { useEffect, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import { useActivityRows } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { wantLiveSteps } from "@/lib/liveRuns";
import { ActivityList, useHeldHrefs } from "./ActivityControl";
import { openAssistant } from "./assistant";
import { shellChatOn } from "./chatFlag";
import { HomeCard } from "./HomeCard";

/** How often the relative times move. */
const TICK_MS = 30_000;

export default function AiWaitingCard({ className = "" }: { className?: string }) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const waiting = useActivityRows(workspace).filter((r) => r.state === "needs_input");
  const heldHrefs = useHeldHrefs();
  const [chatOn] = useState(() => shellChatOn());
  const [now, setNow] = useState(() => Date.now());
  const shown = waiting.length > 0;

  // The run steps and the clock, only while the card shows.
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
    <HomeCard title="AI waiting on you" icon="Sparkles" testId="ai-waiting" className={className}>
      <ActivityList
        rows={waiting}
        now={now}
        visibleHrefs={heldHrefs}
        onOpenChat={chatOn ? (row) => openAssistant({ sessionId: row.threadId }) : undefined}
      />
    </HomeCard>
  );
}
