"use client";

/**
 * The shell of the CRM record page (WS-53 CRM-X4), shared since CRM-Q6b.
 *
 * Three parts, and both record pages draw them: the known records
 * (`RecordSheet`) and the generic Zoho records (`ModuleRecordSheet`). One
 * record page with one look, so a Product reads like a Deal.
 *
 * - `RecordCard`: the shared card frame (`CardPanel`, ORC-1, D181): a side
 *   panel, a centered card, or the whole screen on a phone. Its header is the
 *   Projects header, with the noun of the record, "Open in Zoho" and the title.
 * - `RecordFacts`: the facts of the summary header.
 * - `RecordTabBar`: the tabs under the header, drawn with the shared `Tabs`.
 */

import CardPanel, { useCardStop } from "@/components/CardPanel";
import Tabs from "@/components/Tabs";
import { TaskHeaderRow } from "@/components/TaskPanelHeader";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import type { ReactNode } from "react";
import type { FieldRow } from "../lib/fields";
import FieldValue from "./FieldValue";

/** The words of each record tab. A page draws only the tabs it has. */
export const TAB_LABEL = {
  overview: "Overview",
  timeline: "Timeline",
  related: "Related",
  fields: "Fields",
} as const;

export type ShellTab = keyof typeof TAB_LABEL;

export function RecordCard({
  title,
  noun,
  zoho,
  actions,
  onClose,
  children,
}: {
  title: string;
  /** What the record is, said over its title ("Deal", "Product"). */
  noun: string;
  /** The checked "Open in Zoho" address (`zohoLink`), or null. */
  zoho: string | null;
  /** The record's own controls, in the header row: the steps of `j` and `k`. */
  actions?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const { isMobile } = useViewMode();
  const { stop, expand, asideRef } = useCardStop(isMobile);
  return (
    // The URL owns whether it is open, and every way out (Escape, the scrim,
    // the close button) runs `onClose`.
    <CardPanel stop={stop} label={`${noun}: ${title}`} onClose={onClose} asideRef={asideRef}>
      <header className="shrink-0 border-b border-border bg-card px-3 py-3">
        <TaskHeaderRow
          taskRef={noun}
          noun="record"
          // The URL of the page holds the record, and the list behind it.
          linkFor={() => window.location.href}
          chips={
            zoho ? (
              // The record header carries the way back to Zoho (CRM-Q1).
              <a
                href={zoho}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary underline underline-offset-2"
              >
                Open in Zoho
              </a>
            ) : null
          }
          actions={actions}
          expand={expand}
          onClose={onClose}
        />
        {/* Wraps and never cuts: the name is what the member opened this for. */}
        <h2 className="break-words text-base font-semibold leading-snug text-foreground">{title}</h2>
      </header>
      {children}
    </CardPanel>
  );
}

/** A record that did not load, with the way to try again. Both sheets draw it. */
export function RecordLoadFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 p-8 text-center">
      <p className="text-xs text-muted-foreground">This record did not load. Try again.</p>
      <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

/**
 * The facts of the summary header (CRM-X4 item 1). Nothing for no fact.
 *
 * The card decides, not the viewport (ORC-1): in a narrow card the facts take
 * their own line, because beside the stage pill they cut an amount to
 * "₹11,00,…". The row that holds them is the `@container`.
 */
export function RecordFacts({ facts }: { facts: FieldRow[] }) {
  if (facts.length === 0) return null;
  return (
    <dl className="flex min-w-0 flex-1 basis-full flex-wrap items-center gap-x-4 gap-y-1 @lg:basis-0">
      {facts.map((fact) => (
        <div key={fact.key} className="flex min-w-0 items-baseline gap-1.5">
          <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {fact.label}
          </dt>
          <dd className="min-w-0 truncate text-xs text-foreground">
            <FieldValue value={fact.value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The tabs of the record page, kept in `?rtab=`. */
export function RecordTabBar<T extends ShellTab>({
  tabs,
  active,
  onTab,
}: {
  tabs: readonly T[];
  active: T;
  onTab: (tab: T) => void;
}) {
  return (
    <div className="shrink-0 border-b border-border px-2">
      <Tabs
        chrome="inline"
        variant="underline"
        tabs={tabs.map((t) => ({ id: t, label: TAB_LABEL[t], guideAnchor: t === "timeline" }))}
        activeTab={active}
        onTabChange={(t) => onTab(t as T)}
      />
    </div>
  );
}
