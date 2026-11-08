"use client";

/**
 * AppRequestCard — the ONE card of an app request (WS-MCP MCP-P1b-1).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §19.5 ("The card") and
 * D4 and D8 of the MCP-P1b audit. An outside agent, or a Studio write, asks
 * the member to approve one change. This card shows the stored call, and the
 * member presses Approve or Decline. Nothing runs on an approve: the caller
 * spends the request later, once.
 *
 * One card for every caller. It takes a `request` prop, so the list "Waiting
 * for you" in Integrations, AI agents, the Studio door and the chat can each
 * mount it. It draws through `RollupCard` as `ConfirmationCard` does, with
 * the same title row, icon and button tones. It does not wrap
 * `ConfirmationCard`, because that card reads a chat event.
 *
 * The rules of D8, each held by `AppRequestCard.test.ts`:
 *
 * 1. **Show all or refuse.** Each value shows whole. The server refuses a
 *    call over 32 KiB of JSON, so the card never cuts one.
 * 2. **Server frame, agent values.** The title, the class line, the client
 *    and the expiry are the server's words. Each value of the call sits in
 *    one block under the mark "Text from the agent. Check it." It is a React
 *    text node, never inner HTML and never Markdown.
 * 3. **Text that other people wrote** is a value too, under the same mark.
 *
 * A new card ignores a click for `ARM_MS`, as the approval card of the chat
 * does, so the second click of a double click never decides a card that the
 * member did not see.
 */

import { useEffect, useState } from "react";

import CardFieldValue from "@/components/CardFieldValue";
import { ARM_MS } from "@/components/ConfirmationCard";
import Icon from "@/components/Icon";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import {
  AGENT_TEXT_MARK,
  REQUEST_HINT,
  REQUEST_TITLE,
  askLine,
  canDecide,
  expiryText,
  payloadRows,
  stateWords,
  type AppRequest,
  type AppRequestDecision,
  type AppRequestState,
} from "@/lib/appRequest";

const STATE_TONE: Record<AppRequestState, BadgeTone> = {
  awaiting: "primary",
  approved: "success",
  declined: "neutral",
  done: "success",
  expired: "neutral",
};

export interface AppRequestCardProps {
  request: AppRequest;
  /** Approve or Decline. The parent sends the decision. */
  onDecide: (decision: AppRequestDecision) => void;
  /** The decision on its way, so its button shows the verb and a spinner. */
  busy?: AppRequestDecision | null;
  /** Why the last decision did not land. Approve and Decline try again. */
  error?: string | null;
  /**
   * One line beside the state of a decided request, such as what became of
   * the answer that the chat card sends (STU-B2c-2c). Server text never
   * goes here.
   */
  note?: string | null;
  /** The clock of the expiry, for a test. */
  now?: number;
}

/** The card, with no state of its own. `armed` is false for `ARM_MS`. */
export function AppRequestCardView({
  request,
  onDecide,
  busy = null,
  error = null,
  note = null,
  now = Date.now(),
  armed,
}: AppRequestCardProps & { armed: boolean }) {
  const open = canDecide(request, now);
  const rows = payloadRows(request.call);
  const hard = request.class === "C";
  const decide = (decision: AppRequestDecision) => {
    if (armed && !busy) onDecide(decision);
  };
  return (
    <RollupCard id={`app-request:${request.request_id}`} title={REQUEST_TITLE} icon="ShieldCheck" header="own" pending={open}>
      <section
        aria-label={request.card.title || REQUEST_TITLE}
        data-app-request-card={request.request_id}
        className={`${CARD_FRAME_CLASS} glass:glass-card-tile glass:[--tile:var(--system-orange)] glass:rounded-[16px] glass:border-primary/35 glass:glass-raised glass:p-4`}
      >
        <RollupHeader />
        <RollupBody className="space-y-2 pt-1.5">
          <div className="min-w-0">
            <p className="break-words text-xs font-medium leading-relaxed text-foreground">
              {request.card.title || request.op}
            </p>
            <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{askLine(request)}</p>
            {request.card.class_line && (
              <p
                className={`mt-0.5 flex items-start gap-1 break-words text-[11px] ${hard ? "text-warning" : "text-muted-foreground"}`}
                data-app-request-class={request.class ?? ""}
              >
                {hard && <Icon name="AlertTriangle" size={12} className="mt-px shrink-0" />}
                {request.card.class_line}
              </p>
            )}
          </div>

          <div data-app-request-payload="">
            <p className="mb-1 flex items-center gap-1 text-[11px] text-muted-foreground">
              <Icon name="Bot" size={12} className="shrink-0" />
              {AGENT_TEXT_MARK}
            </p>
            <dl className="space-y-1 rounded-md border border-border bg-background/60 px-2.5 py-2">
              {rows.length === 0 && <div className="text-xs text-muted-foreground">The change holds no values.</div>}
              {rows.map((row) => (
                <div key={row.key} className="flex gap-3 text-xs" data-app-request-field={row.key}>
                  <dt className="w-24 shrink-0 pt-px text-muted-foreground">{row.label}</dt>
                  <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground">
                    <CardFieldValue value={row.value} fenced={false} />
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {open ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                icon="Check"
                loading={busy === "approve"}
                disabled={busy !== null}
                aria-disabled={!armed || undefined}
                onClick={() => decide("approve")}
                data-app-request-approve=""
              >
                {busy === "approve" ? "Approving…" : "Approve"}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={busy === "decline"}
                disabled={busy !== null}
                aria-disabled={!armed || undefined}
                onClick={() => decide("decline")}
                data-app-request-decline=""
              >
                {busy === "decline" ? "Declining…" : "Decline"}
              </Button>
              {error ? (
                <span role="alert" className="text-[10px] text-destructive" data-app-request-error="">
                  {error}
                </span>
              ) : (
                <span className="text-[10px] text-muted-foreground">
                  {REQUEST_HINT} {expiryText(request.expires_at, now)}.
                </span>
              )}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={Object.hasOwn(STATE_TONE, request.state) ? STATE_TONE[request.state] : "neutral"} size="xs">
                {request.state === "awaiting" ? stateWords("expired") : stateWords(request.state)}
              </Badge>
              {note && (
                <span role="status" className="min-w-0 break-words text-[10px] text-muted-foreground" data-app-request-note="">
                  {note}
                </span>
              )}
            </div>
          )}
        </RollupBody>
      </section>
    </RollupCard>
  );
}

export default function AppRequestCard(props: AppRequestCardProps) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setArmed(true), ARM_MS);
    return () => clearTimeout(t);
  }, []);
  return <AppRequestCardView {...props} armed={armed} />;
}
