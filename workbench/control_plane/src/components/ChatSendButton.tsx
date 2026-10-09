"use client";

import Icon from "@/components/Icon";

/**
 * The chat composer's Send button: one control, used by the composer and by
 * the edit of the last message (owner, 2026-10-09: "keep the send button
 * identical to the normal composer's"). Before this file the edit drew its
 * own coral "↑ Send" pill, which no other surface used.
 *
 * Fence: `components/chatEditComposer.test.ts`.
 */
export default function ChatSendButton({
  disabled,
  onClick,
  type = "submit",
  label = "Send",
  title = "Send message",
}: {
  disabled?: boolean;
  onClick?: () => void;
  type?: "submit" | "button";
  label?: string;
  title?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="shrink-0 self-end h-9 w-9 rounded-xl bg-primary text-primary-foreground flex items-center justify-center disabled:opacity-25 disabled:cursor-not-allowed hover:opacity-90 tech-transition"
      aria-label={label}
      title={title}
    >
      <Icon name="ArrowUp" size={16} strokeWidth={2.5} />
    </button>
  );
}
