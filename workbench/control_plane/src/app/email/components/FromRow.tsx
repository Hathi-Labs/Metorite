"use client";

/**
 * The From row of a composer — WS-17 EM-T8c, §11.4 and §11.7.3 of
 * `project-docs/specs/email_app_master_plan.md`, decision D-EM-20.
 *
 * The sending mailbox is visible and never silent. The row shows when the
 * member has two or more mailboxes: the chip of the sending mailbox and the
 * house dropdown (`SelectButton`) of every mailbox, by label with the address
 * as its hint. A mailbox that cannot send until a reconnect is offered but
 * not choosable, and the composer blocks a send from it. One warning at a time
 * shows under the row, with a one-click switch to the mailbox that fits
 * better (`fromWarning` in `lib/mailbox.ts`).
 */

import Button from "@/components/ui/Button";
import { SelectButton, type SelectOption } from "@/components/ui/SelectButton";
import type { EmailAccount } from "../lib/types";
import { mailboxLabel, sendBlocked, type FromWarning } from "../lib/mailbox";
import { MailboxChip } from "./MailboxChip";

/** The options of the From dropdown. A mailbox that cannot send stays in the
 *  list, disabled, with the reason as its hint, unless it is the current one,
 *  so the member can see why the send stops. */
export function fromOptions(
  accounts: ReadonlyArray<EmailAccount>,
  value: string,
  authErrors: Readonly<Record<string, string>>,
): SelectOption[] {
  return accounts.map((a) => {
    const blocked = sendBlocked(a, authErrors);
    return {
      value: a.id,
      label: mailboxLabel(a),
      hint: blocked ? `${a.emailAddress} · reconnect to send` : a.emailAddress,
      keywords: a.emailAddress,
      disabled: blocked && a.id !== value,
    };
  });
}

export function FromRow({
  accounts,
  value,
  defaultValue,
  onChange,
  warning,
  authErrors,
  labelClassName = "text-xs text-muted-foreground w-8 flex-shrink-0",
}: {
  accounts: EmailAccount[];
  value: string;
  /** The mailbox the composer opened on. Off it, the dropdown shows a single
   *  arrow, so the member sees that the From changed. */
  defaultValue?: string;
  onChange: (accountId: string) => void;
  warning: FromWarning | null;
  authErrors: Readonly<Record<string, string>>;
  /** The style of the "From" label, so the row lines up with its composer. */
  labelClassName?: string;
}) {
  if (accounts.length < 2) return null;
  const current = accounts.find((a) => a.id === value) ?? null;
  const blocked = sendBlocked(current, authErrors);
  const switchTo = warning?.switchTo;
  return (
    <div className="space-y-1">
      <div className="flex min-w-0 items-center gap-2">
        <span className={labelClassName}>From</span>
        {current ? <MailboxChip account={current} /> : null}
        <SelectButton
          label="Send from"
          value={value}
          defaultValue={defaultValue ?? value}
          options={fromOptions(accounts, value, authErrors)}
          widthClass="max-w-[18rem]"
          onChange={onChange}
        />
      </div>
      {blocked && current ? (
        <p role="alert" className="text-[11px] text-destructive">
          Reconnect {mailboxLabel(current)} to send from it, or choose another mailbox.
        </p>
      ) : warning ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-foreground">
          <span className="min-w-0 flex-1">{warning.text}</span>
          {switchTo ? (
            <Button
              variant="text"
              size="none"
              layout=""
              type="button"
              className="whitespace-nowrap px-1"
              onClick={() => onChange(switchTo)}
            >
              Send from {warning.switchLabel}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
