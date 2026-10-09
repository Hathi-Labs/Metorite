"use client";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

/**
 * The chat composer's Send button: one control, used by the composer and by
 * the edit of the last message (owner, 2026-10-09: "keep the send button
 * identical to the normal composer's"). Before this file the edit drew its
 * own coral "↑ Send" pill, which no other surface used.
 *
 * It is the `Button` primitive, so the control tokens (focus ring, state
 * layer) apply. `radius="keep"` keeps the composer's `rounded-xl` corner.
 *
 * Fence: `components/chatEditComposer.test.ts`.
 */
export default function ChatSendButton({
  disabled,
  loading,
  onClick,
  type = "submit",
  label = "Send",
  title = "Send message",
}: {
  disabled?: boolean;
  loading?: boolean;
  onClick?: () => void;
  type?: "submit" | "button";
  label?: string;
  title?: string;
}) {
  return (
    <Button
      type={type}
      variant="primary"
      size="none"
      radius="keep"
      onClick={onClick}
      disabled={disabled}
      loading={loading}
      aria-label={label}
      title={title}
      className="shrink-0 self-end h-9 w-9 rounded-xl"
    >
      {loading ? null : <Icon name="ArrowUp" size={16} strokeWidth={2.5} />}
    </Button>
  );
}
