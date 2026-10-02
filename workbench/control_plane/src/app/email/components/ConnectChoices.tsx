"use client";

/**
 * The provider choices of the connect flow (WS-17 EM-T3b).
 *
 * The empty state and the add-account dialog draw the same list, from
 * `CONNECT_PROVIDERS` in `lib/connect.ts`. One list, so the two places
 * cannot offer different providers.
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
 *   retry is a first connect and must carry the range.
 * - After "Back", focus goes back to the provider that opened the step.
 *   `Button` takes no `ref`, so the list finds it by `data-provider`.
 */

import { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  CONNECT_PROVIDERS,
  rememberImportMonths,
  storedImportMonths,
  type ConnectProviderId,
} from "../lib/connect";
import { ImportRangeStep } from "./ImportRangeStep";

type Step = { provider: ConnectProviderId; months: number };

export function ConnectChoices({
  onConnect,
  initialProvider = null,
}: {
  /** Starts the sign-in. `importMonths` is the range the member chose. */
  onConnect: (provider: ConnectProviderId, importMonths: number) => void;
  /** Opens the range step of this provider at once. A live provider only. */
  initialProvider?: ConnectProviderId | null;
}) {
  // This component mounts only on the client, after the account read
  // settles, so the storage read in the initializer cannot differ from a
  // server render.
  const [step, setStep] = useState<Step | null>(() =>
    initialProvider ? { provider: initialProvider, months: storedImportMonths() } : null,
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
      {CONNECT_PROVIDERS.map((p) => (
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
        </li>
      ))}
    </ul>
  );
}
