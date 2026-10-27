"use client";

/**
 * What /crm shows before it has a mode (WS-53 CRM-U5, spec §3.1, §8.3).
 *
 * Two cards for an admin. "Use Metorite as your CRM" sets Native and opens
 * the pipeline. "Connect your CRM" starts the Zoho consent in one click: the
 * customer never configures an OAuth app, because Metorite owns it. With the
 * mirror off there is no second card. A member with no mode waits for an admin.
 *
 * The app's title bar stays over the cards, as in the Email connect page.
 * The decisions are in `../lib/connect.ts` (`firstOpen`).
 */

import type { ReactNode } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

import {
  CRM_OFF_TITLE,
  NO_ACCESS_BODY,
  NO_ACCESS_TITLE,
  NO_FEATURE_BODY,
  NO_FEATURE_TITLE,
} from "../lib/access";
import type { FirstOpen as FirstOpenPhase } from "../lib/connect";

/** The CRMs on the Connect card. Only Zoho is live. */
export const CONNECT_PROVIDERS = [
  { id: "zoho", label: "Zoho CRM", live: true },
  { id: "hubspot", label: "HubSpot", live: false },
  { id: "salesforce", label: "Salesforce", live: false },
  { id: "pipedrive", label: "Pipedrive", live: false },
] as const;

export function CrmFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <AppTopBar title="CRM" />
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </div>
  );
}

export default function FirstOpen({
  phase,
  busy,
  onNative,
  onConnectZoho,
}: {
  phase: Exclude<FirstOpenPhase, "ready">;
  /** Which action is running, so its button shows a spinner. */
  busy: "native" | "zoho" | null;
  onNative: () => void;
  onConnectZoho: () => void;
}) {
  if (phase === "loading") {
    return (
      <CrmFrame>
        <div className="mx-auto flex max-w-3xl flex-col gap-3 p-6" aria-busy="true">
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-40 w-full" />
        </div>
      </CrmFrame>
    );
  }

  if (phase === "no-access") {
    return (
      <CrmFrame>
        <EmptyState icon="Lock" message={NO_ACCESS_TITLE} hint={NO_ACCESS_BODY} />
      </CrmFrame>
    );
  }

  if (phase === "no-feature") {
    return (
      <CrmFrame>
        <EmptyState icon="Lock" message={NO_FEATURE_TITLE} hint={NO_FEATURE_BODY} />
      </CrmFrame>
    );
  }

  if (phase === "off") {
    return (
      <CrmFrame>
        <EmptyState icon="PowerOff" message={CRM_OFF_TITLE} />
      </CrmFrame>
    );
  }

  if (phase === "wait-admin") {
    return (
      <CrmFrame>
        <EmptyState
          icon="Lock"
          message="An admin must set up the CRM first."
          hint="Ask an admin of your workspace to choose how the CRM works."
        />
      </CrmFrame>
    );
  }

  const withMirror = phase === "choose";
  return (
    <CrmFrame>
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-8">
        <h2 className="text-lg font-semibold text-foreground">Set up your CRM</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Choose where your leads, deals and contacts live. You can change this later in Settings.
        </p>

        <div className={`mt-5 grid gap-4 ${withMirror ? "md:grid-cols-2" : ""}`}>
          <section className="flex flex-col rounded-lg border border-border bg-card p-5">
            <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Icon name="Briefcase" size={20} />
            </span>
            <h3 className="text-base font-semibold text-foreground">Use Metorite as your CRM</h3>
            <p className="mt-1 flex-1 text-sm text-muted-foreground">
              Start with an empty pipeline. Your team creates leads, deals and contacts here.
            </p>
            <Button
              className="mt-4 self-start"
              variant="primary"
              loading={busy === "native"}
              disabled={busy !== null}
              onClick={onNative}
            >
              Use Metorite
            </Button>
          </section>

          {withMirror && (
            <section className="flex flex-col rounded-lg border border-border bg-card p-5">
              <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
                <Icon name="Plug" size={20} />
              </span>
              <h3 className="text-base font-semibold text-foreground">Connect your CRM</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Keep your CRM where it is. Metorite reads it and shows it here. Records stay
                read-only, and you can add notes and calls.
              </p>
              <ul className="mt-3 flex-1 space-y-2">
                {CONNECT_PROVIDERS.map((provider) => (
                  <li key={provider.id} className="flex items-center gap-2 text-sm text-foreground">
                    <span className="flex-1">{provider.label}</span>
                    {provider.live ? (
                      <Button
                        variant="primary"
                        size="sm"
                        loading={busy === "zoho"}
                        disabled={busy !== null}
                        onClick={onConnectZoho}
                      >
                        Connect
                      </Button>
                    ) : (
                      <>
                        <Badge>Coming soon</Badge>
                        <Button variant="secondary" size="sm" disabled>
                          Connect
                        </Button>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </CrmFrame>
  );
}
