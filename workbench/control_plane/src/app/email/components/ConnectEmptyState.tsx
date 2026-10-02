"use client";

/**
 * "Connect your email" — what the Email app shows with no mailbox (EM-T3b).
 *
 * It is part of the page, not a dialog. A member with no mailbox has nothing
 * behind a dialog to go back to, so a dialog only added a "Maybe later" that
 * led to an empty three-pane shell.
 *
 * ⚠️ There is no setup step here. The deployment owns the Microsoft app
 * (§10.5), so a member never configures OAuth, and nothing under `app/email`
 * links to Integrations (EM-T3b done-when 1).
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import type { ConnectProviderId } from "../lib/connect";
import { ConnectChoices } from "./ConnectChoices";

export function ConnectEmptyState({
  onConnect,
  loadError,
  onRetry,
}: {
  /** Starts the sign-in with the range the member chose (EM-T6d). */
  onConnect: (provider: ConnectProviderId, importMonths: number) => void;
  /** Set when the account list could not load. Then "no mailbox" is not known. */
  loadError?: string | null;
  onRetry?: () => void;
}) {
  if (loadError) {
    return (
      <div className="flex h-full w-full items-center justify-center overflow-y-auto bg-background p-4">
        <div className="w-full max-w-md rounded-lg border border-border bg-card p-6 text-center">
          <span className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <Icon name="AlertCircle" size={20} />
          </span>
          <h2 className="text-base font-semibold text-foreground">We could not load your mailboxes</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Your mail is safe. Metorite could not reach the server. Try again in a moment.
          </p>
          {onRetry && (
            <Button className="mt-4" variant="primary" icon="RefreshCw" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full items-center justify-center overflow-y-auto bg-background p-4">
      <div className="w-full max-w-md">
        <div className="rounded-lg border border-border bg-card p-6">
          <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon name="Mail" size={22} />
          </span>
          <h2 className="text-lg font-semibold text-foreground">Connect your email</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Bring your inbox into Metorite. The assistant sorts what arrives, drafts replies in your
            voice and turns requests into tasks.
          </p>

          <div className="mt-5">
            <ConnectChoices onConnect={onConnect} />
          </div>

          <ul className="mt-5 space-y-1.5 border-t border-border pt-4 text-xs text-muted-foreground">
            <li className="flex items-start gap-2">
              <Icon name="ShieldCheck" size={14} className="mt-0.5 shrink-0 text-success" />
              <span>You sign in with Microsoft. Metorite never sees your password.</span>
            </li>
            <li className="flex items-start gap-2">
              <Icon name="Lock" size={14} className="mt-0.5 shrink-0 text-success" />
              <span>You can disconnect the mailbox at any time from the account menu.</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
