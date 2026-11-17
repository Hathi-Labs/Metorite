"use client";

/**
 * The read state of a mail row (EM-RU, task 3688).
 *
 * Two signals, never mixed. UNREAD is a state of the mail: a bar of 3 px in
 * the accent at the left edge, a semibold sender and subject, and an accent
 * time. READ is quiet. SELECTED is where you are: a fill, and never a bar.
 * The bar and the weight carry the state, so colour is never the only cue.
 */

import type { MouseEvent } from "react";

import Button from "@/components/ui/Button";
import type { Email } from "../lib/types";

/** One change of state fades over the motion token. Reduced motion is at once. */
const FADE =
  "transition-[color,font-weight,opacity] duration-[var(--motion-duration)] ease-[var(--motion-easing)] motion-reduce:transition-none";

/** The classes of the sender, the subject and the time of a row. */
export function readTone(isRead: boolean, isSelected = false): { sender: string; subject: string; time: string } {
  return isRead
    ? {
        sender: `font-normal text-foreground/75 ${FADE}`,
        subject: `font-normal text-foreground/65 ${FADE}`,
        time: `font-normal text-muted-foreground ${FADE}`,
      }
    : {
        sender: `font-semibold text-foreground ${FADE}`,
        subject: `font-semibold text-foreground ${FADE}`,
        // The accent on the selected fill is 4.25:1 in light mode, so the time
        // of a selected unread row takes the foreground and keeps the weight.
        time: `font-semibold ${isSelected ? "text-foreground" : "text-primary"} ${FADE}`,
      };
}

/**
 * The classes of the open button of a row. Selection is a fill and has no
 * left border, so a selected row never looks like an unread one.
 */
export function rowButtonClass(isSelected: boolean): string {
  return `flex-1 min-w-0 text-left pr-3 pl-1 py-3 transition-colors flex flex-col gap-1 select-none ${
    isSelected ? "bg-primary/10" : "hover:bg-secondary/50"
  }`;
}

/** The bar at the left edge. It stays mounted, so it can fade when the mail turns read. */
export function UnreadBar({ isRead }: { isRead: boolean }) {
  return (
    <span
      aria-hidden="true"
      data-unread-bar={isRead ? "off" : "on"}
      className={`pointer-events-none absolute left-0 inset-y-1 w-[3px] rounded-full bg-primary ${FADE} ${
        isRead ? "opacity-0" : "opacity-100"
      }`}
    />
  );
}

/** The first words of the accessible name of an unread row. */
export function UnreadLabel({ isRead }: { isRead: boolean }) {
  return isRead ? null : <span className="sr-only">Unread, </span>;
}

/**
 * The handler of the toggle. It calls the one update path once and never opens the mail.
 */
export function readToggleHandler(
  email: Pick<Email, "id" | "isRead">,
  update: (id: string, updates: { isRead: boolean }) => unknown,
) {
  return (e: Pick<MouseEvent, "stopPropagation" | "preventDefault">) => {
    e.stopPropagation();
    e.preventDefault();
    void update(email.id, { isRead: !email.isRead });
  };
}

/**
 * The envelope button. It shows on row hover and on keyboard focus, and it
 * stays hidden on a touch screen, where the long-press menu keeps the act.
 */
export function ReadToggle({
  email,
  update,
}: {
  email: Pick<Email, "id" | "isRead">;
  update: (id: string, updates: { isRead: boolean }) => unknown;
}) {
  const label = email.isRead ? "Mark as unread" : "Mark as read";
  return (
    <span className="absolute right-2 bottom-2 z-10 opacity-0 group-hover:opacity-100 focus-within:opacity-100 pointer-coarse:hidden">
      <Button
        variant="ghost"
        size="icon-sm"
        radius="keep"
        layout="flex items-center justify-center"
        icon={email.isRead ? "Mail" : "MailOpen"}
        title={label}
        aria-label={label}
        onClick={readToggleHandler(email, update)}
        className="min-h-6 min-w-6 rounded-md bg-card"
      />
    </span>
  );
}
