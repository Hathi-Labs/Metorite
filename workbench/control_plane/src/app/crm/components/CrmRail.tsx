"use client";

/**
 * The CRM's rail of views (WS-53 CRM-U1, CRM-Q8).
 *
 * One `RailRow` per view, as the Projects tree, the My Tasks lists and the
 * Email folders draw theirs (AGENTS.md rule 12). The settings row sits apart
 * below a divider, because it changes the pipeline and does not show data.
 *
 * Since WS-53 CRM-U8 the saved views no longer nest under the entity rows.
 * They are chips in row 1 of each list (`ViewChips`), where the member
 * saves, renames, shares and deletes them.
 *
 * Since WS-53 CRM-Q6b the rail also holds one row for each mirrored generic
 * Zoho module (Products, Quotes, each custom module), under the known views
 * and in the profile order (`railEntries`). With no module there is no row.
 *
 * Since WS-53 CRM-Q20-2 a core list that a Zoho module plays keeps its row
 * and label, and the row opens the module's list (`?tab=module&module=`).
 * The module's own row leaves the group (`railEntries`).
 *
 * Since WS-53 CRM-Q17b (crm_platform.md §18.9.6 items 1 and 2) the generic
 * modules fold into one group, "More modules", with the modules that hold
 * records first (`railSections`). The group opens by itself while one of its
 * modules is on screen, and its rows scroll inside a bounded box, so Reports
 * and CRM settings stay in view. A core row that Zoho turned off and that
 * holds no record leaves the rail (`hiddenCoreRow`, through `states`).
 *
 * Since WS-53 CRM-C1 the settings row opens its sections as child rows while
 * the settings tab is on screen (`SettingsSectionRows`, crm_platform.md
 * §20.4). Each child is a `RailRow` at depth 1, as a saved view is. While a
 * section is open, the child row takes the highlight from the settings row.
 *
 * Since WS-53 CRM-Q32-3 (§18.14.5) the "CRM settings" row and the "Open
 * questions" row show the open count of an admin, with `metaTone="attention"`.
 * The page passes the count only to an admin, and the count hides at 0.
 *
 * The page owns where the rail goes: a column beside the content on a
 * desktop, and the shell's drawer on a phone. This file draws the rows only.
 * Fence: `src/lib/railRows.test.ts`.
 */

import Icon from "@/components/Icon";
import RailRow from "@/components/ui/RailRow";
import { useState } from "react";

import type { SettingsSection, SettingsSectionId } from "../lib/settingsSections";
import type { EntitySlug } from "../lib/types";
import type { CrmModule } from "../lib/modules";
import type { CrmView } from "../lib/urlState";
import {
  CRM_SETTINGS_VIEW,
  MODULE_GROUP_LABEL,
  railEntries,
  railSections,
  type CoreStates,
  type CrmRailView,
  type RailEntry,
  type SourceLabels,
} from "../lib/views";

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
  /**
   * CRM-Q32-3: the open questions of an admin. `null` or absent draws no
   * count. The settings row and the "Open questions" row show it.
   */
  questions?: number | null;
};

/**
 * The child rows of the settings row: one `RailRow` at depth 1 for each
 * section. No hook, so the test can read its element tree.
 */
export function SettingsSectionRows({ sections, active, onSelect, questions = null }: RailSettings) {
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
            meta={section.id === "questions" ? questions : null}
            metaTone="attention"
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
  modules,
  settings,
  labels = null,
  overlays = null,
  states = null,
}: {
  tab: CrmView["tab"];
  onSelect: (tab: CrmView["tab"]) => void;
  modules?: RailModules;
  /** The settings sections. They show while the settings tab is on screen. */
  settings?: RailSettings;
  /**
   * The source's names of the core entities (CRM-Q17a-2 item 7). In Mirror
   * mode Contacts can read "Existing Sites". `null` keeps the CRM's names.
   */
  labels?: SourceLabels | null;
  /**
   * CRM-Q20-2 (option A): the Zoho module that each core list opens, from
   * `GET /crm/mode`. The row keeps its label and opens the module's list.
   */
  overlays?: Partial<Record<EntitySlug, string>> | null;
  /**
   * CRM-Q17b item 2: the state of each core entity, from `GET /crm/mode`. A
   * row that Zoho turned off and that holds no record leaves the rail.
   */
  states?: CoreStates | null;
}) {
  // The fold of the module group. `null` follows the screen: open while a
  // module of the group is on screen. A click of the member wins.
  const [groupOpen, setGroupOpen] = useState<boolean | null>(null);

  const row = (view: CrmRailView, module?: string) => {
    // So does an open settings section (CRM-C1).
    const sections = view.id === "settings" && tab === "settings" ? settings : undefined;
    const sectionOpen = Boolean(sections?.sections.some((s) => s.id === sections.active));
    // CRM-Q20-2: a list that a module plays opens the module, and its row
    // is the one that holds the highlight while that module is on screen.
    const selected = module
      ? tab === "module" && modules?.active === module
      : tab === view.id && !sectionOpen;
    return (
      <div key={view.id} className="flex flex-col gap-0.5">
        <RailRow
          label={view.label}
          selected={selected}
          // CRM-Q32-3: the open questions of an admin, on the settings row.
          meta={view.id === "settings" ? (settings?.questions ?? null) : null}
          metaTone="attention"
          icon={<Icon name={view.icon} className="h-4 w-4 shrink-0" />}
          buttonProps={{ "aria-current": selected ? "page" : undefined }}
          onSelect={() => (module ? modules?.onSelect(module) : onSelect(view.id))}
        />
        {sections ? <SettingsSectionRows {...sections} /> : null}
      </div>
    );
  };

  const sections = railSections(railEntries(modules?.modules, labels, overlays, states));
  const viewEntry = (entry: RailEntry) =>
    entry.kind === "view" ? row(entry.view, entry.module) : null;
  const activeInGroup =
    tab === "module" && sections.modules.some((m) => m.module === modules?.active);
  const open = groupOpen ?? activeInGroup;

  return (
    <div className="flex flex-col gap-0.5">
      {sections.head.map(viewEntry)}
      {sections.modules.length > 0 ? (
        <div className="flex flex-col gap-0.5" data-crm-module-group="">
          <RailRow
            label={MODULE_GROUP_LABEL}
            tier="group"
            meta={sections.modules.length}
            // Folded over the module on screen, the group holds the highlight.
            selected={!open && activeInGroup}
            icon={<Icon name="Boxes" className="h-4 w-4 shrink-0" />}
            expand={{ expanded: open, onToggle: () => setGroupOpen(!open) }}
            buttonProps={{ "aria-expanded": open }}
            onSelect={() => setGroupOpen(!open)}
          />
          {open ? (
            // Bounded, so Reports and CRM settings stay in view (item 1).
            <div className="flex max-h-64 flex-col gap-0.5 overflow-y-auto" role="group" aria-label={MODULE_GROUP_LABEL}>
              {sections.modules.map((m) => {
                const selected = tab === "module" && modules?.active === m.module;
                return (
                  <RailRow
                    key={`module:${m.module}`}
                    label={m.label}
                    depth={1}
                    guides
                    meta={m.count}
                    selected={selected}
                    icon={<Icon name={m.icon} className="h-3.5 w-3.5 shrink-0" />}
                    buttonProps={{ "aria-current": selected ? "page" : undefined }}
                    onSelect={() => modules?.onSelect(m.module)}
                  />
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
      {sections.tail.map(viewEntry)}
      <div className="my-1.5 h-px bg-border" aria-hidden />
      {row(CRM_SETTINGS_VIEW)}
    </div>
  );
}
