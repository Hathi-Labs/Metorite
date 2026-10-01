"use client";

/**
 * Disconnect a mailbox, from the account menu inside Email (§10.3 step 8).
 *
 * Built on the shared `ConfirmDialog`, so it has the `Modal` scrim, the focus
 * trap, Escape and focus return. The words come from `disconnectCopy` in
 * `lib/connect.ts` and say what really happens: the gateway deletes the
 * account row, and every synced table cascades from it.
 *
 * A refusal stays in the dialog as text, and the store re-reads the
 * accounts, so the list behind the dialog is the server's.
 */

import { useState } from "react";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { disconnectCopy } from "../lib/connect";
import type { EmailAccount } from "../lib/types";

export function DisconnectDialog({
  account,
  onDisconnect,
  onClose,
}: {
  /** The mailbox to disconnect. `null` closes the dialog. */
  account: EmailAccount | null;
  onDisconnect: (id: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const copy = disconnectCopy(account?.emailAddress ?? "this mailbox");

  const close = () => {
    if (busy) return;
    setFailed(false);
    onClose();
  };

  const confirm = async () => {
    if (!account) return;
    setBusy(true);
    setFailed(false);
    const ok = await onDisconnect(account.id);
    setBusy(false);
    if (ok) onClose();
    else setFailed(true);
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
      {failed ? (
        <p role="alert" className="text-xs text-destructive">
          Metorite could not disconnect the mailbox. Try again.
        </p>
      ) : null}
    </ConfirmDialog>
  );
}
