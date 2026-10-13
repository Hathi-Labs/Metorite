"use client";

/**
 * The CRM's rail of views (WS-53 CRM-U1, CRM-Q8).
 *
 * One `RailRow` per view, as the Projects tree, the My Tasks lists and the
 * Email folders draw theirs (AGENTS.md rule 12). The saved views of each
 * entity nest under its row (spec section 8.1). The settings row sits apart
 * below a divider, because it changes the pipeline and does not show data.
 *
 * Since WS-53 CRM-Q6b the rail also holds one row for each mirrored generic
 * Zoho module (Products, Quotes, each custom module), under the known views
 * and in the profile order (`railEntries`). With no module there is no row.
 *
 * Since WS-53 CRM-C1 the settings row opens its sections as child rows while
 * the settings tab is on screen (`SettingsSectionRows`, crm_platform.md
 * §20.4). Each child is a `RailRow` at depth 1, as a saved view is. While a
 * section is open, the child row takes the highlight from the settings row.
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
import type { SettingsSection, SettingsSectionId } from "../lib/settingsSections";
import type { EntitySlug } from "../lib/types";
import type { CrmModule } from "../lib/modules";
import type { CrmView } from "../lib/urlState";
import { CRM_SETTINGS_VIEW, railEntries, type CrmRailView } from "../lib/views";

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

/** The generic modules of the rail (CRM-Q6b). */
export type RailModules = {
  /** `GET /crm/modules`, or null while unread or on a failure. */
  modules: readonly CrmModule[] | null;
  /** The module on screen, when the view is a module list. */
  active: string | null;
  onSelect: (module: string) => void;
};

/** The sections of CRM settings, as child rows of its rail row (CRM-C1). */
export type RailSettings = {
  /** The sections the caller can open, in rail order (`visibleSections`). */
  sections: readonly SettingsSection[];
  /** The section on screen, or null on another tab. */
  active: SettingsSectionId | null;
  onSelect: (section: SettingsSectionId) => void;
};

/**
 * The child rows of the settings row: one `RailRow` at depth 1 for each
 * section. No hook, so the test can read its element tree.
 */
export function SettingsSectionRows({ sections, active, onSelect }: RailSettings) {
  return (
    <>
      {sections.map((section) => {
        const selected = active === section.id;
        return (
          <RailRow
            key={section.id}
            label={section.label}
            depth={1}
            guides
            selected={selected}
            icon={<Icon name={section.icon} className="h-3.5 w-3.5 shrink-0" />}
            buttonProps={{ "aria-current": selected ? "page" : undefined }}
            onSelect={() => onSelect(section.id as SettingsSectionId)}
          />
        );
      })}
    </>
  );
}

export default function CrmRail({
  tab,
  onSelect,
  saved,
  modules,
  settings,
}: {
  tab: CrmView["tab"];
  onSelect: (tab: CrmView["tab"]) => void;
  saved?: RailViewActions;
  modules?: RailModules;
  /** The settings sections. They show while the settings tab is on screen. */
  settings?: RailSettings;
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
    // So does an open settings section (CRM-C1).
    const sections = view.id === "settings" && tab === "settings" ? settings : undefined;
    const sectionOpen = Boolean(sections?.sections.some((s) => s.id === sections.active));
    const selected = tab === view.id && !viewInEffect && !sectionOpen;
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
        {sections ? <SettingsSectionRows {...sections} /> : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-0.5">
      {railEntries(modules?.modules).map((entry) => {
        if (entry.kind === "view") return row(entry.view);
        const selected = tab === "module" && modules?.active === entry.row.module;
        return (
          <RailRow
            key={`module:${entry.row.module}`}
            label={entry.row.label}
            meta={entry.row.count}
            selected={selected}
            icon={<Icon name={entry.row.icon} className="h-4 w-4 shrink-0" />}
            buttonProps={{ "aria-current": selected ? "page" : undefined }}
            onSelect={() => modules?.onSelect(entry.row.module)}
          />
        );
      })}
      <div className="my-1.5 h-px bg-border" aria-hidden />
      {row(CRM_SETTINGS_VIEW)}
    </div>
  );
}
