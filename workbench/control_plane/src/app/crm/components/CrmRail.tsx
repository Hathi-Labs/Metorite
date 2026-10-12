"use client";

/**
 * The CRM's rail of views (WS-53 CRM-U1, CRM-Q8).
 *
 * One `RailRow` per view, as the Projects tree, the My Tasks lists and the
 * Email folders draw theirs (AGENTS.md rule 12). The saved views of each
 * entity nest under its row (spec section 8.1). The settings row sits apart
 * below a divider, because it changes the pipeline and does not show data.
 *
 * The page owns where the rail goes: a column beside the content on a
 * desktop, and the shell's drawer on a phone. This file draws the rows only.
 * Fence: `src/lib/railRows.test.ts`.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import RailRow from "@/components/ui/RailRow";
import { useState } from "react";

import { canEditView, railViews, type SavedView } from "../lib/savedViews";
import type { EntitySlug } from "../lib/types";
import type { CrmView } from "../lib/urlState";
import { CRM_SETTINGS_VIEW, CRM_VIEWS, type CrmRailView } from "../lib/views";

export type RailViewActions = {
  views: Record<EntitySlug, SavedView[]>;
  /** The saved view in effect (`?view=`). */
  activeViewId: string | null;
  isAdmin: boolean;
  onSelectView: (view: SavedView) => void;
  onRenameView: (view: SavedView, name: string) => void;
  onShareView: (view: SavedView) => void;
  onDeleteView: (view: SavedView) => void;
};

export default function CrmRail({
  tab,
  onSelect,
  saved,
}: {
  tab: CrmView["tab"];
  onSelect: (tab: CrmView["tab"]) => void;
  saved?: RailViewActions;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const viewRow = (view: SavedView, actions: RailViewActions) => {
    const editable = canEditView(view, actions.isAdmin);
    const active = actions.activeViewId === view.id;
    return (
      <RailRow
        key={view.id}
        label={view.name}
        depth={1}
        guides
        selected={active}
        icon={<Icon name={view.shared ? "Users" : "Bookmark"} className="h-3.5 w-3.5 shrink-0" />}
        buttonProps={{ "aria-current": active ? "page" : undefined }}
        onSelect={() => actions.onSelectView(view)}
        editor={
          renaming === view.id ? (
            <Input
              autoFocus
              inputSize="sm"
              value={draft}
              aria-label={`Rename ${view.name}`}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => setRenaming(null)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setRenaming(null);
                if (e.key === "Enter" && draft.trim()) {
                  actions.onRenameView(view, draft.trim());
                  setRenaming(null);
                }
              }}
            />
          ) : undefined
        }
        actions={
          editable ? (
            <>
              <Button
                variant="ghost"
                size="icon-xs"
                icon="Pencil"
                aria-label={`Rename ${view.name}`}
                onClick={() => {
                  setDraft(view.name);
                  setRenaming(view.id);
                }}
              />
              {actions.isAdmin ? (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  icon={view.shared ? "Lock" : "Users"}
                  aria-label={view.shared ? `Make ${view.name} private` : `Share ${view.name}`}
                  onClick={() => actions.onShareView(view)}
                />
              ) : null}
              <Button
                variant="ghost"
                size="icon-xs"
                icon="Trash2"
                aria-label={`Delete ${view.name}`}
                onClick={() => actions.onDeleteView(view)}
              />
            </>
          ) : undefined
        }
      />
    );
  };

  const row = (view: CrmRailView) => {
    const nested =
      saved && view.id in saved.views ? railViews(saved.views[view.id as EntitySlug]) : [];
    // A saved view in effect takes the highlight from its entity row.
    const viewInEffect = nested.some((v) => v.id === saved?.activeViewId);
    const selected = tab === view.id && !viewInEffect;
    return (
      <div key={view.id} className="flex flex-col gap-0.5">
        <RailRow
          label={view.label}
          selected={selected}
          icon={<Icon name={view.icon} className="h-4 w-4 shrink-0" />}
          buttonProps={{ "aria-current": selected ? "page" : undefined }}
          onSelect={() => onSelect(view.id)}
        />
        {saved ? nested.map((v) => viewRow(v, saved)) : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-0.5">
      {CRM_VIEWS.map(row)}
      <div className="my-1.5 h-px bg-border" aria-hidden />
      {row(CRM_SETTINGS_VIEW)}
    </div>
  );
}
