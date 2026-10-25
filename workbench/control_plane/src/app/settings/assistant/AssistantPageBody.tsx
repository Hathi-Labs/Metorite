/**
 * The body of /settings/assistant for each state of the switch (WS-63 PZ-25,
 * `personalization.md` §14.2). A pure view, so each state has a test.
 *
 * - "pending": a skeleton, as the pane draws while it loads.
 * - "error": the read failed, so the page cannot say yes or no. It says so,
 *   with Retry, and never "not available", which would be a claim.
 * - "off": the page is not available for this build or this organization.
 * - "on": the children, which is the pane.
 *
 * Fence: `AssistantPageBody.test.ts`.
 */

import type { ReactNode } from "react";

import Button from "@/components/ui/Button";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import type { ProfileState } from "@/lib/useAssistantProfileEnabled";

export function AssistantPageBody({
  state,
  onRetry,
  children,
}: {
  state: ProfileState;
  onRetry: () => void;
  children?: ReactNode;
}) {
  if (state === "on") return <div className="mx-auto max-w-3xl">{children}</div>;
  if (state === "pending") {
    return (
      <div className="mx-auto max-w-3xl space-y-4" data-testid="assistant-page-loading">
        <Skeleton className="h-6 w-1/3" />
        <SkeletonRows count={5} />
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="mx-auto max-w-3xl">
        <div role="alert" className="space-y-3 rounded-lg border border-border bg-card p-4">
          <p className="text-sm text-foreground">This page did not load.</p>
          <p className="text-xs text-muted-foreground">Try again.</p>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={onRetry}>
            Retry
          </Button>
        </div>
      </div>
    );
  }
  return <p className="text-xs text-muted-foreground">This page is not available yet.</p>;
}
