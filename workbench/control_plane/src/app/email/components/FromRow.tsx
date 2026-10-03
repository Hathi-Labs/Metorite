"use client";

/**
 * The From row of a composer — WS-17 EM-T8c, §11.4 and §11.7.3 of
 * `project-docs/specs/email_app_master_plan.md`, decision D-EM-20.
 *
 * The sending mailbox is visible and never silent. The row shows when the
 * member has two or more mailboxes: the chip of the sending mailbox and a
 * picker of every mailbox. A mailbox that needs a reconnect cannot send, so
 * the picker marks it and the composer blocks the send. One warning at a time
 * shows under the row, with a one-click switch to the mailbox that fits
 * better (`fromWarning` in `lib/mailbox.ts`).
 */

import Button from "@/components/ui/Button";
import { Select } from "@/components/ui/Input";
import type { EmailAccount } from "../lib/types";
import { needsReconnect, type FromWarning } from "../lib/mailbox";
import { MailboxChip, mailboxLabel } from "./MailboxChip";

export function FromRow({
  accounts,
  value,
  onChange,
  warning,
  authErrors,
  labelClassName = "text-xs text-muted-foreground w-8 flex-shrink-0",
}: {
  accounts: EmailAccount[];
  value: string;
  onChange: (accountId: string) => void;
  warning: FromWarning | null;
  authErrors: Readonly<Record<string, string>>;
  /** The style of the "From" label, so the row lines up with its composer. */
  labelClassName?: string;
}) {
  if (accounts.length < 2) return null;
  const current = accounts.find((a) => a.id === value) ?? null;
  const blocked = needsReconnect(current, authErrors);
  return (
    <div className="space-y-1">
      <div className="flex min-w-0 items-center gap-2">
        <span className={labelClassName}>From</span>
        {current ? <MailboxChip account={current} /> : null}
        <Select
          inputSize="sm"
          className="min-w-0 flex-1"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Send from"
        >
          {accounts.map((a) => {
            const reconnect = needsReconnect(a, authErrors);
            return (
              <option key={a.id} value={a.id} disabled={reconnect && a.id !== value}>
                {`${mailboxLabel(a)} · ${a.emailAddress}${reconnect ? " (reconnect to send)" : ""}`}
              </option>
            );
          })}
        </Select>
      </div>
      {blocked && current ? (
        <p role="alert" className="text-[11px] text-destructive">
          Reconnect {mailboxLabel(current)} to send from it, or choose another mailbox.
        </p>
      ) : warning ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-foreground">
          <span className="min-w-0 flex-1">{warning.text}</span>
          <Button
            variant="ghost"
            size="none"
            layout=""
            type="button"
            className="whitespace-nowrap px-1 text-[11px] font-medium text-primary"
            onClick={() => onChange(warning.switchTo)}
          >
            Send from {warning.switchLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
