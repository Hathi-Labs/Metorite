"use client";

/**
 * The CRM filter bar and the saved-view controls (WS-53 CRM-Q8, CRM-S2).
 *
 * One row of controls above a list or a board: the search box (a list only,
 * CRM-X1), owner, source, the custom picklist fields and the date ranges.
 * Then the view row: "Save view", and
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
import { filterWord } from "@/lib/shell/registry";
import { useEffect, useRef, useState } from "react";

import type { FieldDef } from "../lib/fields";
import {
  ANY,
  SOURCE_OPTIONS,
  activeCount,
  dateFilters,
  optionsOf,
  ownerOptions,
  picklistDefs,
  searchCommitter,
  setCustom,
  setRange,
  type CrmOwner,
} from "../lib/filterBar";
import { clearFilters, type SavedView } from "../lib/savedViews";
import type { EntitySlug } from "../lib/types";
import type { CrmView } from "../lib/urlState";
import { viewLabel } from "../lib/views";

export type FilterBarProps = {
  entity: EntitySlug;
  view: CrmView;
  onChange: (next: CrmView) => void;
  /**
   * The owners of `GET /crm/owners` (CRM-X1): each active member, and each
   * record owner who is not a member. `null` while unread or on a failure.
   */
  owners?: ReadonlyArray<CrmOwner> | null;
  /**
   * Draw the search box (CRM-X1). A list only: the board read takes no `q`,
   * so a box on a board would change nothing.
   */
  search?: boolean;
  fieldDefs: FieldDef[] | null;
  /** Mirror mode: a synced record keeps no created date, so the range is hidden. */
  mirror?: boolean;
  /** Filters dropped before the request, by name. */
  removed?: string[];
  /** The custom filters could not be checked, so the list was not read. */
  unchecked?: boolean;
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
  owners = null,
  search = false,
  fieldDefs,
  mirror = false,
  removed = [],
  unchecked = false,
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
        {search ? (
          <SearchBox
            label={viewLabel(entity).toLowerCase()}
            value={view.q}
            onCommit={(q) => {
              if (q !== view.q) onChange({ ...view, q });
            }}
          />
        ) : null}
        <SelectButton
          label="Owner"
          widthClass="w-40"
          className={view.owner ? OFF_DEFAULT : ""}
          value={view.owner ?? ANY}
          defaultValue={ANY}
          onChange={(next) => onChange({ ...view, owner: next || null })}
          options={ownerOptions(owners, view.owner)}
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

      {unchecked ? (
        <p role="status" className="mt-2 text-[11px] text-destructive">
          Could not check the custom filters. Reload to try again.
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

/**
 * The list's search box (CRM-X1). It writes `?q=` once, 300 ms after the last
 * key, through the page's `go`, so each search is one history entry and Back
 * gives the last query back.
 *
 * The text is local while the member types. A `?q=` that this box did not
 * write (Back, Clear, a saved view) replaces the text. Its own write does not,
 * so a key typed while the URL catches up is never lost.
 *
 * `data-page-filter` lets the shell reach it: `/` focuses it, and "Show all in
 * …" fills it (`src/lib/shell/pageFilter.ts`).
 */
function SearchBox({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string;
  onCommit: (q: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  const [mine, setMine] = useState<string | null>(null);
  if (value !== seen) {
    setSeen(value);
    if (value !== mine) setDraft(value);
    setMine(null);
  }
  const commit = useRef(onCommit);
  useEffect(() => {
    commit.current = onCommit;
  });
  // Made on the first key, in the handler, so no render reads a ref.
  const box = useRef<ReturnType<typeof searchCommitter> | null>(null);
  useEffect(() => {
    const timer = box;
    return () => timer.current?.cancel();
  }, []);
  return (
    <Input
      icon="Search"
      inputSize="sm"
      className="w-56"
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        box.current ??= searchCommitter((q) => {
          setMine(q);
          commit.current(q);
        });
        box.current.type(e.target.value);
      }}
      placeholder={`${filterWord()} ${label}…`}
      aria-label={`${filterWord()} ${label}`}
      data-page-filter={label}
    />
  );
}
