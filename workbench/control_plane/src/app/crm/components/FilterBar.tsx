"use client";

/**
 * The CRM filter bar and the saved-view controls (WS-53 CRM-Q8, CRM-S2).
 *
 * One row of controls above a list or a board: owner, source, the custom
 * picklist fields and the date ranges. Then the view row: "Save view", and
 * "Update view / Reset" when a saved view is in effect and the screen has moved
 * away from it. It is the Projects `FilterBar` pattern (`SelectButton` for a
 * choice, the house `Button`, the same off-default tint). It adds no second look.
 *
 * The choices live in `lib/filterBar.ts` and the view rules in
 * `lib/savedViews.ts`. This file is markup and local state only.
 */

import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";
import { useState } from "react";

import type { FieldDef } from "../lib/fields";
import {
  ANY,
  SOURCE_OPTIONS,
  activeCount,
  dateFilters,
  optionsOf,
  ownerOptions,
  picklistDefs,
  setCustom,
  setRange,
} from "../lib/filterBar";
import { clearFilters, type SavedView } from "../lib/savedViews";
import type { EntitySlug } from "../lib/types";
import type { CrmView } from "../lib/urlState";

export type FilterBarProps = {
  entity: EntitySlug;
  view: CrmView;
  onChange: (next: CrmView) => void;
  /** The rows on screen, to offer the owners that hold records. */
  rows: ReadonlyArray<Record<string, unknown>>;
  fieldDefs: FieldDef[] | null;
  /** Mirror mode: a synced record keeps no created date, so the range is hidden. */
  mirror?: boolean;
  /** Filters dropped before the request, by name. */
  removed?: string[];
  /** The saved view in effect, or null. */
  activeView: SavedView | null;
  /** The screen differs from the saved view. */
  dirty: boolean;
  /** The caller may change the saved view in effect. */
  canEdit: boolean;
  isAdmin: boolean;
  saving: boolean;
  onSave: (name: string, shared: boolean) => Promise<void>;
  onUpdate: () => Promise<void>;
  onReset: () => void;
};

export default function FilterBar({
  entity,
  view,
  onChange,
  rows,
  fieldDefs,
  mirror = false,
  removed = [],
  activeView,
  dirty,
  canEdit,
  isAdmin,
  saving,
  onSave,
  onUpdate,
  onReset,
}: FilterBarProps) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [share, setShare] = useState(false);
  const picklists = picklistDefs(fieldDefs);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    await onSave(trimmed, isAdmin && share);
    setName("");
    setShare(false);
    setNaming(false);
  }

  return (
    <div className="shrink-0 border-b border-border px-3 py-2 sm:px-4" data-testid="crm-filter-bar">
      <div className="flex flex-wrap items-center gap-2">
        <SelectButton
          label="Owner"
          widthClass="w-40"
          className={view.owner ? OFF_DEFAULT : ""}
          value={view.owner ?? ANY}
          defaultValue={ANY}
          onChange={(next) => onChange({ ...view, owner: next || null })}
          options={ownerOptions(rows, view.owner)}
        />
        <SelectButton
          label="Source"
          widthClass="w-36"
          className={view.source ? OFF_DEFAULT : ""}
          value={view.source ?? ANY}
          defaultValue={ANY}
          onChange={(next) => onChange({ ...view, source: next || null })}
          options={[{ value: ANY, label: "Any source" }, ...SOURCE_OPTIONS]}
        />
        {picklists.map((def) => {
          const chosen = view.custom[def.key] ?? [];
          const options = optionsOf(def);
          const known = chosen.every((c) => options.some((o) => o.value === c));
          return (
            <SelectButton
              key={def.key}
              label={def.label}
              widthClass="w-40"
              className={chosen.length > 0 ? OFF_DEFAULT : ""}
              value={chosen[0] ?? ANY}
              defaultValue={ANY}
              onChange={(next) => onChange(setCustom(view, def.key, next))}
              options={[
                { value: ANY, label: `Any ${def.label.toLowerCase()}` },
                ...options,
                // A saved view can name an option that the field no longer lists.
                ...(known || chosen.length === 0
                  ? []
                  : [{ value: chosen[0], label: chosen.join(", ") }]),
              ]}
            />
          );
        })}
        {dateFilters(entity, mirror).map((range) => (
          <div
            key={range.label}
            className="flex items-center gap-1"
            role="group"
            aria-label={`${range.label} dates`}
          >
            <span className="text-xs text-muted-foreground">{range.label}</span>
            <Input
              type="date"
              inputSize="sm"
              aria-label={`${range.label} from`}
              value={view.ranges[range.from] ?? ""}
              onChange={(e) => onChange(setRange(view, range.from, e.target.value))}
            />
            <Input
              type="date"
              inputSize="sm"
              aria-label={`${range.label} to`}
              value={view.ranges[range.to] ?? ""}
              onChange={(e) => onChange(setRange(view, range.to, e.target.value))}
            />
          </div>
        ))}
        {activeCount(view) > 0 || view.statusId || view.q ? (
          <Button variant="ghost" size="sm" icon="X" onClick={() => onChange(clearFilters(view))}>
            Clear
          </Button>
        ) : null}
      </div>

      {removed.length > 0 ? (
        <p role="status" className="mt-2 text-[11px] text-muted-foreground">
          A filter was removed: {removed.join(", ")} is not available here.
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {naming ? (
          <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
            <Input
              autoFocus
              inputSize="sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setNaming(false);
              }}
              placeholder="View name"
              aria-label="View name"
            />
            {isAdmin ? (
              <label className="flex items-center gap-1 text-xs text-foreground">
                <Checkbox
                  checked={share}
                  onChange={() => setShare((s) => !s)}
                  aria-label="Share with the organization"
                />
                Share with everyone
              </label>
            ) : null}
            <Button type="submit" size="sm" loading={saving}>
              Save
            </Button>
          </form>
        ) : (
          <Button
            variant="text"
            size="sm"
            icon="Bookmark"
            onClick={() => {
              setName(activeView && dirty ? `${activeView.name} copy` : "");
              setNaming(true);
            }}
          >
            {activeView && dirty ? "Save as new" : "Save view"}
          </Button>
        )}
        {activeView && dirty && !naming ? (
          <>
            <span className="text-[11px] text-foreground">
              <span className="font-medium">{activeView.name}</span> has unsaved changes.
            </span>
            {canEdit ? (
              <Button size="sm" icon="Save" loading={saving} onClick={() => void onUpdate()}>
                Update view
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" icon="RotateCcw" onClick={onReset}>
              Reset
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}
