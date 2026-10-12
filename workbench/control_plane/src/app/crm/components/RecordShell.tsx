"use client";

/**
 * The shell of the CRM record page (WS-53 CRM-X4), shared since CRM-Q6b.
 *
 * Three parts, and both record pages draw them: the known records
 * (`RecordSheet`) and the generic Zoho records (`ModuleRecordSheet`). One
 * record page with one look, so a Product reads like a Deal.
 *
 * - `RecordModal`: the drawer at the right edge (a sheet on a phone), with the
 *   title, the noun of the record and "Open in Zoho".
 * - `RecordFacts`: the facts of the summary header.
 * - `RecordTabBar`: the tabs under the header, drawn with the shared `Tabs`.
 */

import Tabs from "@/components/Tabs";
import Modal from "@/components/ui/Modal";
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

export function RecordModal({
  title,
  noun,
  zoho,
  onClose,
  children,
}: {
  title: string;
  /** What the record is, said under its title ("Deal", "Product"). */
  noun: string;
  /** The checked "Open in Zoho" address (`zohoLink`), or null. */
  zoho: string | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const { isMobile } = useViewMode();
  return (
    // On `Modal` (CRM-U2), as a drawer at the right edge on a desktop and as
    // a sheet from the bottom on a phone. The URL owns whether it is open, and
    // every way out (Escape, the scrim, the close button) runs `onClose`.
    <Modal
      open
      onClose={onClose}
      title={title}
      description={
        <>
          {noun}
          {zoho && (
            // The record header carries the way back to Zoho (CRM-Q1).
            <>
              {" · "}
              <a
                href={zoho}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-2"
              >
                Open in Zoho
              </a>
            </>
          )}
        </>
      }
      size="4xl"
      placement={isMobile ? "sheet" : "end"}
      closeLabel="Close record"
      className="h-full"
    >
      {children}
    </Modal>
  );
}

/** The facts of the summary header (CRM-X4 item 1). Nothing for no fact. */
export function RecordFacts({ facts }: { facts: FieldRow[] }) {
  if (facts.length === 0) return null;
  return (
    <dl className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
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
        tabs={tabs.map((t) => ({ id: t, label: TAB_LABEL[t] }))}
        activeTab={active}
        onTabChange={(t) => onTab(t as T)}
      />
    </div>
  );
}
