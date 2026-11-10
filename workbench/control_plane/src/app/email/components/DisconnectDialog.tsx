"use client";

/**
 * Disconnect a mailbox, from the account menu inside Email (§10.3 step 8).
 *
 * Built on the shared `ConfirmDialog`, so it has the `Modal` scrim, the focus
 * trap, Escape and focus return. The words come from `disconnectCopy` in
 * `lib/connect.ts` and say what really happens: the gateway deletes the
 * account row, and every synced table cascades from it.
 *
 * The dialog names the mailbox as "label · address". When that mailbox is the
 * default, it names the mailbox that becomes the default, before the removal
 * (EM-T8f-2, §11.6 case 18). `disconnectNames` in `lib/mailboxSettings.ts`
 * decides both, with `nextDefaultAfter`, which the store reads too.
 *
 * A refusal stays in the dialog as text, and the store re-reads the
 * accounts, so the list behind the dialog is the server's. The text is the
 * reason of the gateway when it gives one: a 409 says that a sync is still
 * writing mail, and the member can try again (EM-T4f).
 */

import { useState } from "react";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { disconnectCopy, type DisconnectOutcome } from "../lib/connect";
import { disconnectNames, holdNames } from "../lib/mailboxSettings";
import type { EmailAccount } from "../lib/types";

export function DisconnectDialog({
  account,
  accounts,
  onDisconnect,
  onClose,
}: {
  /** The mailbox to disconnect. `null` closes the dialog. */
  account: EmailAccount | null;
  /** Each mailbox of the member, with the flag of the default. */
  accounts: ReadonlyArray<EmailAccount>;
  onDisconnect: (id: string) => Promise<DisconnectOutcome>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // Hold the names while the disconnect runs. The store drops the mailbox
  // before the dialog closes, and the names read from the list would lose
  // the new default during the close. `holdNames` decides (review F3). Set
  // during render, the adjust-state-on-a-prop-change pattern that
  // `ConfirmDialog` uses.
  const live = disconnectNames(account, accounts);
  const [held, setHeld] = useState(live);
  const names = holdNames(held, live, busy);
  if (names !== held) setHeld(names);
  const copy = disconnectCopy(names.name, names.nextDefault);

  const close = () => {
    if (busy) return;
    setFailure(null);
    onClose();
  };

  const confirm = async () => {
    if (!account) return;
    setBusy(true);
    setFailure(null);
    const outcome = await onDisconnect(account.id);
    setBusy(false);
    if (outcome.ok) onClose();
    else setFailure(outcome.detail);
  };

  return (
    <ConfirmDialog
      open={account !== null}
      title={copy.title}
      body={copy.body}
      note={copy.note}
      confirmLabel={copy.confirm}
      icon="Unplug"
      busy={busy}
      defaultFocus="cancel"
      onConfirm={() => void confirm()}
      onCancel={close}
    >
      {failure ? (
        <p role="alert" className="text-xs text-destructive">
          {failure}
        </p>
      ) : null}
    </ConfirmDialog>
  );
}
