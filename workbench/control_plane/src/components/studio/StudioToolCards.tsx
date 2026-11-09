"use client";

/**
 * StudioToolCards — the approval card of a Studio record change, in the chat
 * (STU-B2c-2c, `project-docs/specs/ai_studio.md` §14 row B2c-2c, §20.4).
 *
 * A record write tool of Mira asks the door first, and the door opens ONE
 * request that waits for the member. This file draws that request in the
 * chat, with the shared `AppRequestCard`, so the member answers it where Mira
 * asked. The list "Waiting for you" of Integrations, AI agents shows the same
 * request through the same card and the same client
 * (`app/integrations/lib/appRequests.ts`).
 *
 * - **The read.** The card reads its request through the BFF
 *   `GET /api/agent-access/requests/[id]`, in the one read cache. After a
 *   reload it reads it again, so a decided or expired request shows its state
 *   and no buttons. A 404 (not the member's request, or the store is dark)
 *   draws nothing.
 * - **The answer.** Approve and Decline post to the decision BFF once
 *   (`decideRequest`). After a good answer, the card sends ONE follow-up
 *   message into the same chat (`openAssistant({ text })`), and Mira's next
 *   turn sends the change again with the request id. Mira never calls the
 *   decision route.
 *
 * Every rule lives in `lib/studio/studioCardState.ts`, and this file only
 * wires it. Mounted by `MessageBubble`, beside `TaskToolCards`,
 * `ProjectToolCards` and `EmailToolCards`. Fences:
 * `lib/studio/studioCardState.test.ts`, `StudioToolCards.test.ts` and
 * `lib/chatCardConformance.test.ts`.
 */

import { useCallback, useRef, useState } from "react";

import { APP_REQUESTS_KEY, decideRequest, readRequest, requestKey } from "@/app/integrations/lib/appRequests";
import AppRequestCard from "@/components/AppRequestCard";
import type { ToolEvent } from "@/components/MarkdownMessage";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { REQUEST_TITLE, type AppRequestDecision } from "@/lib/appRequest";
import { invalidate } from "@/lib/dataCache";
import { openAssistant, useAskOutcome } from "@/lib/shell/assistant";
import {
  TELL_LABEL,
  askedRequestIds,
  cardNote,
  pressDecision,
  pressTell,
  rememberCard,
  rememberedCard,
  shownRequest,
  tellShows,
  type CardLocal,
} from "@/lib/studio/studioCardState";
import { useCachedResource } from "@/lib/useCachedResource";

/** The words of a refusal that gave no sentence of its own. */
const NOT_SAVED = "Your answer was not saved. Try again.";
/** The space of one card in the turn, after the other tool cards. */
const SLOT_CLASS = "mt-3 min-w-0";
/** The words of a read that failed. Never the raw error. */
const LOAD_FAILED = "The approval did not load. Check the connection, then try again.";

/** The card of ONE request that a write tool asked for. */
export function StudioRequestCard({ requestId, sessionId }: { requestId: string; sessionId?: string }) {
  const read = useCachedResource(requestKey(requestId), () => readRequest(requestId));
  const [local, setLocal] = useState<CardLocal>(() => rememberedCard(requestId));
  // The state of a press, read at once: a second press in the same tick sees
  // the first one and sends nothing.
  const localRef = useRef<CardLocal>(local);
  const outcome = useAskOutcome(local.askSeq);
  const set = useCallback(
    (next: CardLocal) => {
      localRef.current = next;
      rememberCard(requestId, next);
      setLocal(next);
    },
    [requestId],
  );

  // The follow-up goes into the card's own chat or into no chat (join only).
  const tell = (text: string) => openAssistant({ text, joinOnly: true, ...(sessionId ? { sessionId } : {}) });

  const onDecide = (decision: AppRequestDecision) => {
    void pressDecision(requestId, decision, () => localRef.current, set, {
      decide: decideRequest,
      tell,
      newKey: () => crypto.randomUUID(),
      refusal: (err) => (err instanceof Error && err.message ? err.message : NOT_SAVED),
      // Read the request again after each answer, refusals too, so the card
      // and the list of Integrations show the state of the server.
    }).finally(() => invalidate(APP_REQUESTS_KEY));
  };

  // 404: not this member's request, or the store is dark for the org. No
  // card, and no empty space in the turn.
  if (read.data === null) return null;
  if (read.data === undefined) {
    // Nothing to show yet: the space of the card, so nothing below it moves.
    if (!read.error) {
      // About the height of the card, so the turns below it do not jump.
      return (
        <div className={SLOT_CLASS}>
          <Skeleton className="h-80 w-full" />
        </div>
      );
    }
    return (
      <div className={SLOT_CLASS}>
        <RollupCard id={`app-request:${requestId}`} title={REQUEST_TITLE} icon="ShieldCheck" header="own">
          <section aria-label={REQUEST_TITLE} data-studio-request-error="" className={CARD_FRAME_CLASS}>
            <RollupHeader />
            <RollupBody className="flex flex-wrap items-center gap-2 pt-1.5">
              <p role="alert" className="text-xs text-destructive">
                {LOAD_FAILED}
              </p>
              <Button variant="secondary" size="sm" icon="RefreshCw" onClick={read.refresh}>
                Try again
              </Button>
            </RollupBody>
          </section>
        </RollupCard>
      </div>
    );
  }
  const shown = shownRequest(read.data, local);
  // An approval that never ran: one control sends the follow-up, once.
  const act = tellShows(shown, local, outcome)
    ? { label: TELL_LABEL, icon: "Send", onClick: () => void pressTell(requestId, () => localRef.current, set, tell) }
    : null;
  return (
    <div className={SLOT_CLASS} data-studio-request={requestId}>
      <AppRequestCard
        request={shown}
        busy={local.busy}
        error={local.error}
        note={cardNote(shown, local, outcome)}
        act={act}
        onDecide={onDecide}
      />
    </div>
  );
}

/**
 * The approval cards of one turn: one for each request that a record write
 * tool asked for. Null when the turn asked for none, so it is inert for every
 * other tool.
 */
export default function StudioToolCards({ toolEvents, sessionId }: { toolEvents?: ToolEvent[]; sessionId?: string }) {
  const ids = askedRequestIds(toolEvents);
  if (ids.length === 0) return null;
  // Each card holds its own space, so a request with no card leaves none.
  return (
    <>
      {ids.map((id) => (
        <StudioRequestCard key={id} requestId={id} sessionId={sessionId} />
      ))}
    </>
  );
}
