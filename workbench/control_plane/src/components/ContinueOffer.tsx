"use client";

/**
 * The pill "Continue in a fresh chat" under the answer of a held chat
 * (`one_assistant_context.md` §9, WT-2). It reuses `SuggestionPills`, so it
 * is the same pill as the follow-ups. The server decides when to offer it
 * (`lib/chatContinue.ts`), and the host (the shell's dock) opens the new chat.
 * A refusal shows as one plain line under the pill.
 */

import { useState } from "react";

import SuggestionPills from "@/components/SuggestionPills";
import {
  CONTINUE_HEADING,
  CONTINUE_LABEL,
  requestContinue,
  type ContinueOpened,
} from "@/lib/chatContinue";

export default function ContinueOffer({
  sessionId,
  onOpened,
}: {
  sessionId: string;
  onOpened: (opened: ContinueOpened) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const tap = async () => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    const result = await requestContinue(sessionId);
    setBusy(false);
    if (result.ok) onOpened(result);
    else setProblem(result.copy);
  };

  return (
    <div data-continue-offer="">
      <SuggestionPills
        suggestions={[{ label: CONTINUE_LABEL, message: CONTINUE_LABEL, icon: "MessageSquarePlus" }]}
        onPick={() => void tap()}
        label={CONTINUE_HEADING}
        align="start"
        disabled={busy}
      />
      {problem ? (
        <p role="status" className="mt-1.5 text-[11px] text-muted-foreground" data-continue-problem="">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
