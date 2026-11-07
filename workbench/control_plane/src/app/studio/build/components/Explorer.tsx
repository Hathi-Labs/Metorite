"use client";

/**
 * The Explorer of the Studio IDE: the tree of the parts of the app (`ai_studio.md` §6.3, G3a).
 * `buildOutline` makes the tree from the draft. A branch opens and closes. A click on a node
 * tells the page which object and which tab to show. The rows are `RailRow`, the one tree row.
 */
import { useState } from "react";

import Icon from "@/components/Icon";
import { RailRow } from "@/components/ui/RailRow";
import type { Outline, OutlineKind, OutlineNode, OutlineTab } from "@/lib/studio/buildOutline";

const ICONS: Record<OutlineKind, string> = {
  app: "Boxes", branch: "Folder", object: "Table", field: "Hash", view: "LayoutGrid", action: "Zap", rule: "ShieldCheck",
  workflow: "Workflow", skill: "Sparkles", code: "Code", none: "Minus",
};

export interface ExplorerProps {
  outline: Outline;
  /** The node that shows as picked: the selected object's id. */
  selectedObject: string | null;
  onPick: (pick: { object: string | null; tab: OutlineTab | null }) => void;
}

export default function Explorer({ outline, selectedObject, onPick }: ExplorerProps) {
  // A branch is open until the member closes it, so a new part shows without a click.
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (!outline.root) {
    return <p role="status" className="p-3 text-xs text-muted-foreground">{outline.hint}</p>;
  }

  const row = (n: OutlineNode, depth: number): React.ReactNode => {
    const kids = n.children;
    const isNone = n.kind === "none";
    const open = !closed.has(n.id);
    const picked = n.kind === "object" && n.object === selectedObject;
    return (
      <div key={n.id}>
        <RailRow
          label={n.label}
          depth={depth}
          tree
          tier={n.kind === "app" || n.kind === "branch" ? "group" : "item"}
          icon={<Icon name={ICONS[n.kind]} size={14} />}
          meta={n.hint}
          selected={picked}
          expand={kids?.length && !isNone ? { expanded: open, onToggle: () => toggle(n.id) } : undefined}
          buttonProps={isNone ? { disabled: true } : undefined}
          onSelect={isNone ? undefined : () => {
            if (kids?.length && (n.kind === "branch" || n.kind === "app")) toggle(n.id);
            onPick({ object: n.object ?? null, tab: n.tab ?? null });
          }}
        />
        {kids?.length && open ? kids.map((k) => row(k, depth + 1)) : null}
      </div>
    );
  };

  return (
    <nav aria-label="Explorer" className="py-1">
      {row(outline.root, 0)}
    </nav>
  );
}
