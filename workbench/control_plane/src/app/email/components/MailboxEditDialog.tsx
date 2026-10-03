"use client";

/**
 * Rename and recolour a mailbox — WS-17 EM-T8b, §11.7.2 item 5 of
 * `project-docs/specs/email_app_master_plan.md`, decision D-EM-21.
 *
 * Reached from the mailbox menu in the left rail. A blank name goes back to
 * the default label, which the gateway derives from the address (§11.4).
 *
 * ⚠️ **A SLOT, never a colour** (DESIGN_SYSTEM rule 7). The swatches write an
 * index into the `--cat-1..12` ramp, as Space settings do. The stored column
 * is 1-based and `accentForSlot` is 0-based. `slotToStored` and
 * `storedToSlot` are the one place the two meet.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { CATEGORICAL_SLOTS, accentForSlot } from "@/lib/categorical";
import type { EmailAccount } from "../lib/types";
import { MailboxChip, mailboxAccent } from "./MailboxChip";

/** The 0-based ramp index of a stored slot (1 to 12), or -1 for none. */
export function storedToSlot(stored: number | null | undefined): number {
  return stored && stored >= 1 && stored <= CATEGORICAL_SLOTS ? stored - 1 : -1;
}

/** The stored slot (1 to 12) of a 0-based ramp index. */
export function slotToStored(index: number): number {
  return index + 1;
}

export interface MailboxEdit {
  /** The name the member typed. Blank means "use the default label". */
  label: string;
  /** The stored slot, 1 to 12. */
  colorSlot: number;
}

interface MailboxEditProps {
  /** The mailbox to edit. `null` closes the dialog. */
  account: EmailAccount | null;
  /** Saves and resolves to null, or to the reason the gateway refused. */
  onSave: (id: string, edit: MailboxEdit) => Promise<string | null>;
  onClose: () => void;
}

export function MailboxEditDialog({ account, onSave, onClose }: MailboxEditProps) {
  if (!account) return null;
  // Keyed on the id: a DIFFERENT mailbox mounts a fresh form, and a refetch
  // of the same row keeps an edit in progress.
  return <MailboxEditForm key={account.id} account={account} onSave={onSave} onClose={onClose} />;
}

function MailboxEditForm({
  account,
  onSave,
  onClose,
}: MailboxEditProps & { account: EmailAccount }) {
  // The field holds the label the member chose. A stored provider name
  // ("Outlook") is not a choice, so the field starts blank for it.
  const [label, setLabel] = useState(() =>
    account.label && account.label === account.displayLabel ? account.label : "",
  );
  const [slot, setSlot] = useState(() => {
    const stored = storedToSlot(account.colorSlot);
    return stored >= 0 ? stored : drawnSlotIndex(account);
  });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const preview = {
    ...account,
    displayLabel: label.trim() || account.displayLabel,
    colorSlot: slotToStored(slot),
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure(null);
    const refused = await onSave(account.id, {
      label: label.trim(),
      colorSlot: slotToStored(slot),
    });
    setBusy(false);
    if (refused) setFailure(refused);
    else onClose();
  };

  return (
    <Modal
      open
      onClose={() => (busy ? undefined : onClose())}
      title="Mailbox name and colour"
      description={account.emailAddress}
      icon="Mail"
      size="sm"
    >
      <form onSubmit={(e) => void submit(e)} className="space-y-4 p-4">
        <div className="space-y-1.5">
          <label htmlFor="mailbox-label" className="text-xs font-medium text-muted-foreground">
            Name
          </label>
          <Input
            id="mailbox-label"
            autoFocus
            value={label}
            maxLength={40}
            placeholder={account.displayLabel || account.emailAddress}
            onChange={(e) => setLabel(e.target.value)}
            aria-label="Mailbox name"
          />
          <p className="text-[11px] text-muted-foreground">
            Leave it blank to use a name from the address.
          </p>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Colour</p>
          <div role="radiogroup" aria-label="Mailbox colour" className="flex flex-wrap gap-1.5">
            {Array.from({ length: CATEGORICAL_SLOTS }, (_, index) => (
              <button
                key={index}
                type="button"
                role="radio"
                aria-checked={index === slot}
                aria-label={`Colour ${index + 1}`}
                onClick={() => setSlot(index)}
                className={`flex h-8 w-8 items-center justify-center rounded-md tech-transition ${
                  index === slot ? "ring-2 ring-primary" : "hover:bg-muted"
                }`}
              >
                <span className={`h-4 w-4 rounded-full ${accentForSlot(index).dot}`} />
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Preview</p>
          <div className="flex items-center gap-2 rounded-md bg-muted/50 px-2 py-1.5">
            <MailboxChip account={preview} />
            <span className="truncate text-[11px] text-muted-foreground">{account.emailAddress}</span>
          </div>
        </div>

        {failure ? (
          <p role="alert" className="text-xs text-destructive">
            {failure}
          </p>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** The ramp index that a mailbox with no stored slot draws today, so the
 *  picker opens on the colour the member sees. */
function drawnSlotIndex(account: Pick<EmailAccount, "id" | "colorSlot">): number {
  const drawn = mailboxAccent(account).dot;
  for (let i = 0; i < CATEGORICAL_SLOTS; i++) if (accentForSlot(i).dot === drawn) return i;
  return 0;
}
