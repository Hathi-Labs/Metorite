"use client";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

/**
 * The chat composer's ONE primary control: Send, or Stop. Used by the
 * composer and by the edit of the last message (owner, 2026-10-09: "keep the
 * send button identical to the normal composer's"). Before this file the
 * edit drew its own coral "↑ Send" pill, which no other surface used.
 *
 * Owner, 2026-10-12: the composer had a red Stop beside a "Send" split
 * button with a send-mode chevron, and it looked untidy. Now one round
 * control changes its face. While a run is live and the box is empty it is
 * Stop (■). As soon as the box has text it is Send (↑), and a send during a
 * run steers it (`lib/chatSteer.ts` `primaryAction`). It is the same filled
 * primary circle in both states, so the member's eye never has to find a
 * second button.
 *
 * It is the `Button` primitive, so the control tokens (focus ring, state
 * layer) apply. `radius="keep"` keeps the round corner.
 *
 * Fences: `components/chatEditComposer.test.ts`, `lib/chatSteer.test.ts`.
 */
export default function ChatSendButton({
  action = "send",
  disabled,
  loading,
  onClick,
  type = "submit",
  label,
  title,
  large = false,
}: {
  /** `stop` draws the Stop face. It is never a submit, so Enter cannot stop. */
  action?: "send" | "stop";
  disabled?: boolean;
  loading?: boolean;
  onClick?: () => void;
  type?: "submit" | "button";
  label?: string;
  title?: string;
  /** The phone's tap size, 44 px (WS-62 CF-8). The desktop keeps 32 px. */
  large?: boolean;
}) {
  const stop = action === "stop";
  const name = label ?? (stop ? "Stop the reply" : "Send");
  return (
    <Button
      type={stop ? "button" : type}
      variant="primary"
      size="none"
      radius="keep"
      onClick={onClick}
      disabled={disabled}
      loading={loading}
      aria-label={name}
      title={title ?? (stop ? "Stop the reply (Esc)" : "Send (Ctrl+Enter)")}
      data-chat-primary={action}
      className={`shrink-0 rounded-full ${large ? "h-11 w-11" : "h-8 w-8"}`}
    >
      {loading ? null : stop ? (
        <Icon name="Square" size={12} strokeWidth={2.5} fill="currentColor" />
      ) : (
        <Icon name="ArrowUp" size={16} strokeWidth={2.5} />
      )}
    </Button>
  );
}
