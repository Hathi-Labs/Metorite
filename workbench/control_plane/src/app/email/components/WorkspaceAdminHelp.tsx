"use client";

/**
 * The help for a Google Workspace admin (WS-17 EM-G8 item 6, §12.4).
 *
 * A company on Google Workspace can block the apps that its admin has not
 * trusted. The admin trusts Metorite once, by its client ID, in the Admin
 * console. Google shows some of these refusals on its own page and never
 * returns (`org_internal`, EM-G7 item 5), so the help shows under the Gmail
 * choice before the click, and on the callback page after
 * `workspace_admin_blocked`.
 *
 * The client ID comes from the gateway (`GET /email/oauth/gmail/app`, EM-G7
 * item 9), never from a constant. A client ID is public: Google shows it in
 * each authorize URL. The words live in `WORKSPACE_ADMIN_HELP` in
 * `lib/connect.ts`.
 *
 * `WorkspaceAdminSteps` only draws, so a test renders it with a client ID.
 * `WorkspaceAdminHelp` reads the client ID and draws the steps.
 */

import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { getMailAppInfo } from "../lib/api";
import { WORKSPACE_ADMIN_HELP } from "../lib/connect";

/**
 * The steps and the client ID. `clientId` is `undefined` while the read
 * runs, and `null` when the deployment has no Google app or the read failed.
 */
export function WorkspaceAdminSteps({ clientId }: { clientId: string | null | undefined }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!clientId) return;
    try {
      await navigator.clipboard.writeText(clientId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 text-left">
      <ol className="space-y-1.5 text-xs text-muted-foreground">
        {WORKSPACE_ADMIN_HELP.steps.map((step, i) => (
          <li key={step} className="flex gap-2">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
              {i + 1}
            </span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
      {clientId === undefined ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Icon name="Loader2" size={14} className="animate-spin" /> {WORKSPACE_ADMIN_HELP.loading}
        </p>
      ) : clientId === null ? (
        <p className="rounded-md border border-border bg-secondary/50 px-3 py-2 text-xs text-muted-foreground">
          {WORKSPACE_ADMIN_HELP.unavailable}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] font-medium text-foreground">{WORKSPACE_ADMIN_HELP.clientIdLabel}</p>
          <p
            className="select-all break-all rounded-md border border-border bg-secondary/50 px-2.5 py-2 font-mono text-[10px] text-muted-foreground"
            aria-label={WORKSPACE_ADMIN_HELP.clientIdLabel}
          >
            {clientId}
          </p>
          <Button
            variant="secondary"
            size="sm"
            icon={copied ? "Check" : "Copy"}
            className="self-start"
            onClick={() => void copy()}
          >
            {copied ? WORKSPACE_ADMIN_HELP.copied : WORKSPACE_ADMIN_HELP.copy}
          </Button>
        </div>
      )}
    </div>
  );
}

/** The steps, with the client ID read from the gateway once on mount. */
export function WorkspaceAdminHelp() {
  const [clientId, setClientId] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    void getMailAppInfo("gmail").then((app) => {
      if (live) setClientId(app ? app.clientId : null);
    });
    return () => {
      live = false;
    };
  }, []);

  return <WorkspaceAdminSteps clientId={clientId} />;
}
