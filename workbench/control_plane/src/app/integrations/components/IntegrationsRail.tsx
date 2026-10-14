"use client";

/**
 * The rail of views for /integrations. One `RailRow` per view, as the CRM
 * draws its rail (AGENTS.md rule 12). The page owns where the rail goes, and
 * which views it holds: a member without `feature:integrations` gets the AI
 * agents view only (`visibleViews`, WS-55 EA-4b).
 */

import Icon from "@/components/Icon";
import RailRow from "@/components/ui/RailRow";

import { INTEGRATION_VIEWS, type RailView, type ViewId } from "../lib/views";

export default function IntegrationsRail({
  view,
  onSelect,
  views = INTEGRATION_VIEWS,
}: {
  view: ViewId;
  onSelect: (view: ViewId) => void;
  views?: RailView[];
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {views.map((v) => (
        <RailRow
          key={v.id}
          label={v.label}
          selected={view === v.id}
          icon={<Icon name={v.icon} className="h-4 w-4 shrink-0" />}
          buttonProps={{ "aria-current": view === v.id ? "page" : undefined }}
          onSelect={() => onSelect(v.id)}
        />
      ))}
    </div>
  );
}
