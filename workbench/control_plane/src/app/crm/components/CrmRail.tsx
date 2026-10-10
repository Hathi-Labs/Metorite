"use client";

/**
 * The CRM's rail of views (WS-53 CRM-U1).
 *
 * One `RailRow` per view, as the Projects tree, the My Tasks lists and the
 * Email folders draw theirs (AGENTS.md rule 12). The settings row sits apart
 * below a divider, because it changes the pipeline and does not show data.
 *
 * The page owns where the rail goes: a column beside the content on a
 * desktop, and the shell's drawer on a phone. This file draws the rows only.
 * Fence: `src/lib/railRows.test.ts`.
 */

import Icon from "@/components/Icon";
import RailRow from "@/components/ui/RailRow";

import type { CrmView } from "../lib/urlState";
import { CRM_SETTINGS_VIEW, CRM_VIEWS, type CrmRailView } from "../lib/views";

export default function CrmRail({
  tab,
  onSelect,
}: {
  tab: CrmView["tab"];
  onSelect: (tab: CrmView["tab"]) => void;
}) {
  const row = (view: CrmRailView) => (
    <RailRow
      key={view.id}
      label={view.label}
      selected={tab === view.id}
      icon={<Icon name={view.icon} className="h-4 w-4 shrink-0" />}
      buttonProps={{ "aria-current": tab === view.id ? "page" : undefined }}
      onSelect={() => onSelect(view.id)}
    />
  );

  return (
    <div className="flex flex-col gap-0.5">
      {CRM_VIEWS.map(row)}
      <div className="my-1.5 h-px bg-border" aria-hidden />
      {row(CRM_SETTINGS_VIEW)}
    </div>
  );
}
