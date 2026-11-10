"use client";

/**
 * The CRM's error boundary (WS-53 CRM-Q3, spec section 18.6).
 *
 * It wraps the CRM settings page. A view that throws shows one sentence and a
 * Reload button, and the rail and the title bar stay alive. `LayoutBoundary`
 * is Projects' boundary with its own words and a Retry that re-renders the same
 * data, which is the wrong cure for a stale tab. A stale tab needs a reload.
 *
 * Fence: `crmQ3.test.ts` renders the fallback and holds the words.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { BOUNDARY_COPY, boundaryMessage } from "../lib/boundary";

type State = { error: Error | null };

export default class CrmBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The stack survives only here. Capture the console when this shows up.
    console.error("[CrmBoundary] the CRM page threw", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return <CrmFallback error={error} />;
  }
}

/** The fallback alone, so a test can render it without a throwing child. */
export function CrmFallback({ error }: { error: unknown }) {
  return (
    <div
      role="alert"
      className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center"
    >
      <Icon name="TriangleAlert" className="h-8 w-8 text-destructive/70" aria-hidden />
      <p className="max-w-sm text-sm text-foreground">{boundaryMessage(error)}</p>
      <Button
        variant="primary"
        size="sm"
        icon="RefreshCw"
        onClick={() => window.location.reload()}
      >
        {BOUNDARY_COPY.button}
      </Button>
    </div>
  );
}
