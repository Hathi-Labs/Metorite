"use client";

/**
 * "CRM setup" — the admin's way back to the first-open choice (WS-53 CRM-U5b).
 *
 * It sits in the Settings view beside Access and Connection. `SetupPanel`
 * only draws. `SetupSection` owns the confirm, the write and the toast. The
 * words and decisions are in `lib/setup.ts`.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";

import * as api from "../lib/api";
import type { ConnectionInfo, CrmMode } from "../lib/connect";
import {
  CONFIRM_BODY,
  CONFIRM_TITLE,
  SETUP_ANCHOR,
  changeSetup,
  setupBlock,
  setupLine,
} from "../lib/setup";
import { invalidateCrmMode } from "../lib/useCrmMode";

export default function SetupPanel({
  mode,
  blocked,
  busy,
  onChange,
}: {
  mode: CrmMode;
  /** Why the button is off, or `null`. */
  blocked: string | null;
  busy: boolean;
  onChange: () => void;
}) {
  return (
    <section
      id={SETUP_ANCHOR}
      className="rounded-xl border border-border bg-card/40"
      aria-label="CRM setup"
    >
      <header className="flex flex-wrap items-center gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">CRM setup</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{setupLine(mode)}</p>
          {blocked && <p className="mt-0.5 text-xs text-muted-foreground">{blocked}</p>}
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={blocked !== null || busy}
          onClick={onChange}
        >
          Change CRM setup
        </Button>
      </header>
    </section>
  );
}

/** The card with its confirm and its write. Admins only: the page gates it. */
export function SetupSection({
  mode,
  connection,
}: {
  mode: CrmMode;
  connection: ConnectionInfo | null;
}) {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    const failed = await changeSetup({ save: api.resetMode, invalidate: invalidateCrmMode });
    setBusy(false);
    setConfirming(false);
    if (failed) toast.show({ key: "crm-setup", variant: "error", title: failed });
  }

  return (
    <>
      <SetupPanel
        mode={mode}
        blocked={setupBlock(mode, connection)}
        busy={busy}
        onChange={() => setConfirming(true)}
      />
      <ConfirmDialog
        open={confirming}
        title={CONFIRM_TITLE}
        body={CONFIRM_BODY}
        confirmLabel="Change CRM setup"
        confirmVariant="primary"
        icon="Settings"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={confirm}
      />
    </>
  );
}
