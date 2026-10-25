"use client";

/**
 * InterviewOffer — the first-chat offer of the "Get to know me" interview
 * (WS-63 PZ-27, `personalization.md` §14.4).
 *
 * It sits in the empty chat, above the starter pills, in the dock and on
 * /chat alike. It shows only when ALL of these hold (`shouldOffer`): the
 * profile is on for this member's organization, the agent is the orchestrator,
 * the chat is not a shared room, the chat has no message, learning is not off, and the member has not started
 * or declined the interview (`interview.started_at`).
 *
 * - "Yes, start" opens a fresh chat and sends the request as the member's turn.
 * - "No thanks" posts `/interview` with no body. The gateway sets `started_at`
 *   and marks no area, so the offer does not come again.
 * - "Not now" hides the offer for this browser session only.
 *
 * The profile is read once through the data cache. Fence: `InterviewOffer.test.ts`.
 */
import { useCallback, useState } from "react";

import Button from "@/components/ui/Button";
import { fetchProfile, startInterview, type Profile } from "@/app/settings/assistant/lib/profile";
import { invalidate } from "@/lib/dataCache";
import { startGetToKnowMe } from "@/lib/shell/interviewMessage";
import { useAssistantProfileEnabled } from "@/lib/useAssistantProfileEnabled";
import { useCachedResource } from "@/lib/useCachedResource";

/** The cache key of the whole profile read. Not the `enabled` key. */
export const OFFER_PROFILE_KEY = "assistant-profile:interview-offer";
/** The session flag of "Not now". */
export const OFFER_HIDDEN_KEY = "metorite:interview-offer:hidden";

/** The pure rule. A profile that did not load never offers. */
export function shouldOffer(
  profile: Pick<Profile, "learning" | "interview"> | null | undefined,
  enabled: boolean,
  messageCount: number,
  isOrchestrator: boolean,
  isRoom = false,
): boolean {
  // A shared room never offers it: the interview is the member's own (§14.4).
  if (!enabled || !isOrchestrator || isRoom || messageCount !== 0 || !profile) return false;
  return profile.learning !== false && !profile.interview?.started_at;
}

/** True when "Not now" was pressed in this browser session. */
export function hiddenThisSession(store: Pick<Storage, "getItem"> | undefined = safeStore()): boolean {
  try {
    return store?.getItem(OFFER_HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function hideForSession(store: Pick<Storage, "setItem"> | undefined = safeStore()): void {
  try {
    store?.setItem(OFFER_HIDDEN_KEY, "1");
  } catch {
    // A blocked store only means the offer may come back on a reload.
  }
}

function safeStore(): Storage | undefined {
  try {
    return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
  } catch {
    return undefined;
  }
}

export default function InterviewOffer({
  isOrchestrator,
  messageCount,
  isRoom = false,
}: {
  isOrchestrator: boolean;
  messageCount: number;
  /** True in a shared room. */
  isRoom?: boolean;
}) {
  const enabled = useAssistantProfileEnabled();
  const wanted = enabled && isOrchestrator && !isRoom && messageCount === 0;
  const read = useCachedResource<Profile>(wanted ? OFFER_PROFILE_KEY : null, () => fetchProfile());
  const [hidden, setHidden] = useState(() => hiddenThisSession());
  const [busy, setBusy] = useState(false);

  const decline = useCallback(async () => {
    setBusy(true);
    try {
      await startInterview();
      invalidate(OFFER_PROFILE_KEY);
      setHidden(true);
    } catch {
      // The offer stays, and "No thanks" can be pressed again.
    } finally {
      setBusy(false);
    }
  }, []);

  if (hidden || !shouldOffer(read.data, enabled, messageCount, isOrchestrator, isRoom)) return null;
  return (
    <div
      role="region"
      aria-label="Get to know me"
      className="relative w-full max-w-sm rounded-xl border border-border bg-card px-4 py-3 text-left"
    >
      <Button
        size="icon"
        variant="ghost"
        icon="X"
        aria-label="Not now"
        className="absolute right-1 top-1"
        onClick={() => {
          hideForSession();
          setHidden(true);
        }}
      />
      <p className="pr-8 text-sm font-medium text-foreground">Want me to get to know you?</p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        I ask one short question a day. Your answers help me write and plan the way you like. You
        see and edit them in Settings.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={() => startGetToKnowMe()}>
          Yes, start
        </Button>
        <Button size="sm" variant="ghost" loading={busy} disabled={busy} onClick={() => void decline()}>
          No thanks
        </Button>
      </div>
    </div>
  );
}
