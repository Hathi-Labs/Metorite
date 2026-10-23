"use client";

/**
 * "Now working on <label>": a line across the transcript where the email
 * work moved to another mailbox. One idiom for the Email app's own switch
 * and for the one assistant's mark (`navigation_shell.md` §7.1.1 rule 6).
 */

import Icon from "@/components/Icon";

export default function MailboxMark({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 my-3" data-mailbox-mark="">
      <div className="flex-1 h-px bg-primary/25" />
      <span className="flex items-center gap-1 text-[10px] text-primary/80 font-medium shrink-0">
        <Icon name="Mail" size={10} /> Now working on {label}
      </span>
      <div className="flex-1 h-px bg-primary/25" />
    </div>
  );
}
