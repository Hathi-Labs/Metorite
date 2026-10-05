"use client";

/**
 * The provider choices of the connect flow (WS-17 EM-T3b).
 *
 * The empty state and the add-account dialog draw the same list, from
 * `connectChoices` in `lib/connect.ts`. One list, so the two places
 * cannot offer different providers.
 *
 * WS-17 EM-G8 (D-EM-35): the capability read of the gateway decides which
 * provider is live. Until the read settles, the list is a skeleton, so a
 * Gmail choice never draws as "Coming soon" and then turns live under the
 * member's pointer. A failed read keeps Microsoft live and Gmail "Coming
 * soon" (`liveProviders`). A live Gmail choice carries one line for a
 * company admin, which opens `WorkspaceAdminHelp` (EM-G8 item 6).
 *
 * A provider that is not available draws as a disabled row with its note
 * as text beside it. A tooltip alone is unreachable from a keyboard and
 * absent on touch (the `EmptyState` rule).
 *
 * A click on a provider opens a second step, the range of the first import
 * (EM-T6d item 2). The step replaces the list in the same place, and
 * "Continue" calls `onConnect` with the chosen range. "Back" shows the list
 * again. Both places get the step, because both draw this component.
 *
 * Fix round 1:
 * - The step opens with the range the member chose last in this tab
 *   (`storedImportMonths`), or 1. "Continue" keeps the range
 *   (`rememberImportMonths`). Both functions catch every storage error.
 * - `initialProvider` opens the step at once. The callback page's "Try
 *   again" sends the member to `/email?connect=1&provider=…`, because that
 *   retry is a first connect and must carry the range. The list mounts only
 *   after the read settles, so the step sees the live set of the read.
 * - After "Back", focus goes back to the provider that opened the step.
 *   `Button` takes no `ref`, so the list finds it by `data-provider`.
 */

import { useEffect, useId, useRef, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  connectChoices,
  rememberImportMonths,
  storedImportMonths,
  WORKSPACE_ADMIN_HELP,
  type ConnectProvider,
  type ConnectProviderId,
  type ProviderAvailability,
} from "../lib/connect";
import { ImportRangeStep } from "./ImportRangeStep";
import { WorkspaceAdminHelp } from "./WorkspaceAdminHelp";

type Step = { provider: ConnectProviderId; months: number };

export function ConnectChoices({
  onConnect,
  initialProvider = null,
  availability,
}: {
  /** Starts the sign-in. `importMonths` is the range the member chose. */
  onConnect: (provider: ConnectProviderId, importMonths: number) => void;
  /** Opens the range step of this provider at once. A live provider only. */
  initialProvider?: ConnectProviderId | null;
  /**
   * The capability read (`connectProviders` in the store). `undefined`
   * while it runs, `null` when it failed.
   */
  availability: ProviderAvailability | null | undefined;
}) {
  if (availability === undefined) {
    return (
      <div role="status" aria-busy="true" aria-label="Loading" className="flex flex-col gap-2">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    );
  }
  return (
    <ChoiceList
      onConnect={onConnect}
      initialProvider={initialProvider}
      choices={connectChoices(availability)}
    />
  );
}

function ChoiceList({
  onConnect,
  initialProvider,
  choices,
}: {
  onConnect: (provider: ConnectProviderId, importMonths: number) => void;
  initialProvider: ConnectProviderId | null;
  choices: readonly ConnectProvider[];
}) {
  // This component mounts only on the client, after the account read and
  // the capability read settle, so the storage read in the initializer
  // cannot differ from a server render. A provider that the read does not
  // offer never opens a step, whatever the URL asked.
  const [step, setStep] = useState<Step | null>(() =>
    initialProvider && choices.some((c) => c.id === initialProvider && c.available)
      ? { provider: initialProvider, months: storedImportMonths() }
      : null,
  );
  const listRef = useRef<HTMLUListElement>(null);
  const backFrom = useRef<ConnectProviderId | null>(null);

  // After "Back", put focus on the provider that opened the step.
  useEffect(() => {
    if (step !== null || backFrom.current === null) return;
    listRef.current
      ?.querySelector<HTMLButtonElement>(`[data-provider="${backFrom.current}"]`)
      ?.focus();
    backFrom.current = null;
  }, [step]);

  if (step) {
    return (
      <ImportRangeStep
        provider={step.provider}
        months={step.months}
        onChange={(months) => setStep({ ...step, months })}
        onBack={() => {
          backFrom.current = step.provider;
          setStep(null);
        }}
        onContinue={() => {
          rememberImportMonths(step.months);
          onConnect(step.provider, step.months);
        }}
      />
    );
  }

  return (
    <ul ref={listRef} className="flex flex-col gap-2">
      {choices.map((p) => (
        <li key={p.id}>
          <Button
            variant={p.available ? "secondary" : "ghost"}
            size="none"
            layout="flex w-full items-center text-left"
            disabled={!p.available}
            data-provider={p.id}
            onClick={() => p.available && setStep({ provider: p.id, months: storedImportMonths() })}
            aria-describedby={p.note ? `connect-note-${p.id}` : undefined}
            className={`gap-3 px-3 py-2.5 ${p.available ? "bg-card hover:bg-primary/5" : "border border-dashed border-border"}`}
          >
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
                p.available ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
              }`}
            >
              <Icon name={p.icon} size={18} />
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm font-medium text-foreground">{p.label}</span>
              <span className="text-xs text-muted-foreground">{p.detail}</span>
            </span>
            {p.available ? (
              <Icon name="ArrowRight" size={14} className="shrink-0 text-muted-foreground" />
            ) : (
              <span
                id={`connect-note-${p.id}`}
                className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
              >
                {p.note}
              </span>
            )}
          </Button>
          {p.id === "gmail" && p.available && <WorkspaceAdminLine />}
        </li>
      ))}
    </ul>
  );
}

/**
 * The one line under a live Gmail choice (EM-G8 item 6). It opens the help
 * for a Google Workspace admin in place. The help reads the client ID only
 * when it opens.
 */
function WorkspaceAdminLine() {
  const [open, setOpen] = useState(false);
  const helpId = useId();
  return (
    <div className="mt-1.5 px-1">
      <Button
        variant="text"
        size="none"
        layout="flex items-start text-left"
        icon="ShieldCheck"
        aria-expanded={open}
        aria-controls={helpId}
        onClick={() => setOpen((o) => !o)}
        className="gap-1.5 text-xs"
      >
        {WORKSPACE_ADMIN_HELP.line}
      </Button>
      {open && (
        <div id={helpId} className="mt-2 rounded-md border border-border bg-card p-3">
          <WorkspaceAdminHelp />
        </div>
      )}
    </div>
  );
}
