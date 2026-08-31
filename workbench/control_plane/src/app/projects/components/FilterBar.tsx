"use client";

/**
 * Projects · the filter bar and saved-view chips (WS-27k).
 *
 * Spec: `project-docs/specs/project_management_app.md` §11.2 item 3.
 *
 * *"My open bugs in Ops, grouped by assignee"* — this is where that sentence
 * gets typed. Filters go to the server (`routes/projects/filters.py` turns them
 * into WHERE clauses, so paging stays correct); grouping is applied in the
 * browser by `lib/grouping.groupTasks`.
 *
 * A saved view is nothing more than these controls' state written to
 * `pm_views.config`, which is why applying one and typing the same thing by
 * hand must produce an identical board — `toConfig`/`fromConfig` are each
 * other's inverse and their round trip is tested.
 *
 * ## The dirty-view row (WS-27ab item 2)
 *
 * Applying a view and then touching a control used to **drop the association**:
 * `activeViewId` was set to `null` on the first keystroke, so the chip went
 * dark and the only way back to what had been saved was to click the chip
 * again and lose the edit. There was no way to say *keep this*.
 *
 * Now the association survives the edit and the bar says so: the chip keeps a
 * dot, and a row appears offering **Update view · Save as new · Reset**.
 * Whether the state has diverged is `grouping.viewDivergence` — ONE pure
 * function reading the `toConfig`/`fromConfig` round trip, never a comparison
 * written here. A comparison in this file would be a second opinion about what
 * a view IS, and the two would disagree the first time the config grew a key.
 */

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { useEffect, useState } from "react";

import type { FieldRow, TagRow, ViewRow } from "../lib/api";
import {
  type BoardLanes,
  EMPTY_FILTERS,
  type Filters,
  GROUP_OPTIONS,
  type GroupBy,
  UNSET,
  describeDivergence,
  isFiltered,
  personLabel,
  viewDivergence,
} from "../lib/grouping";
import { type ViewMode, honoursGroupBy, honoursLanes } from "../lib/commands";
import {
  DEFAULT_SHOWN,
  FIELD_KEYS,
  FIELD_LABELS,
  customFieldKey,
  sameFieldSet,
  toggleField,
} from "../lib/shownFields";
import { byUsage, chipClass } from "../lib/tags";

/** The status categories, labelled. Mirrors the gateway's `STATUS_CATEGORIES`. */
const CATEGORIES: Array<[string, string]> = [
  ["", "Any status"],
  ["backlog", "Backlog"],
  ["todo", "To do"],
  ["in_progress", "In progress"],
  ["done", "Done"],
  ["cancelled", "Cancelled"],
];

const GROUP_LABELS: Record<GroupBy, string> = {
  status: "Status",
  assignee: "Assignee",
  project: "Project",
  importance: "Priority",
  tag: "Tag",
  none: "Nothing",
};

const SELECT =
  "cc-control rounded-lg border border-border bg-background px-2 py-1.5 " +
  "text-xs text-foreground outline-none focus:border-primary/50";

interface Props {
  filters: Filters;
  onFilters: (next: Filters) => void;
  /**
   * The canvas on screen — it decides which grouping axes this bar OFFERS.
   * `honoursGroupBy` / `honoursLanes` own that table; the bar does not carry
   * its own opinion about what a canvas can draw.
   */
  mode: ViewMode;
  /**
   * The selected node's board spans more than one project — i.e. it has
   * descendant projects (`tree.spansMultipleProjects`). False on a leaf, and
   * on a project with no subprojects, where grouping by project would always
   * yield exactly one group.
   */
  spansProjects: boolean;
  groupBy: GroupBy;
  onGroupBy: (next: GroupBy) => void;
  /**
   * WS-27y — the board's second axis and the lane state that travels with a
   * view. The whole object, not just the axis: WS-27ab's divergence check
   * needs the collapsed lanes too, and two props that must agree is one prop
   * too many (`grouping.BoardLanes`'s own note).
   */
  lanes: BoardLanes;
  onSubGroupBy: (next: GroupBy) => void;
  /** The signed-in member's address, for the "Me" option. Empty while loading. */
  me: string;
  /**
   * WS-27af — who the assignee filter offers, from the tasks in this project.
   *
   * ⚠️ Must be built from an UNFILTERED task set (`grouping.assigneesIn` says
   * why): derived from the rows currently on screen it collapses to whoever is
   * already selected, and the filter becomes one you cannot leave.
   */
  people: readonly string[];
  /** WS-27m — the project's registered tags, for the tag row. */
  tags: TagRow[];
  /** WS-27x — the view's shown fields: the table's columns AND the chip gate. */
  shownFields: readonly string[];
  onShownFields: (next: string[]) => void;
  /** WS-27l — the project's custom field definitions, offered in the picker. */
  fields: FieldRow[];
  views: ViewRow[];
  activeViewId: string | null;
  onApplyView: (view: ViewRow) => void;
  onSaveView: (name: string) => void;
  onDeleteView: (view: ViewRow) => void;
  /** WS-27ab — write what is on screen into the view that is applied. */
  onUpdateView: (view: ViewRow) => void;
  /** Saving needs a project to hang the view off. */
  canSave: boolean;
  /**
   * WS-27ae — export the filter that is on screen, with the columns it shows.
   * Lives in this bar because that is where the filter and the field set are
   * chosen: an Export button anywhere else would look like it exported the
   * project rather than the view.
   */
  onExport: () => Promise<void>;
}

export function FilterBar({
  filters,
  onFilters,
  mode,
  spansProjects,
  groupBy,
  onGroupBy,
  lanes,
  onSubGroupBy,
  me,
  people,
  tags,
  shownFields,
  onShownFields,
  fields,
  views,
  activeViewId,
  onApplyView,
  onSaveView,
  onDeleteView,
  onUpdateView,
  canSave,
  onExport,
}: Props) {
  const subGroupBy = lanes.subGroupBy;

  /**
   * The axes worth offering here.
   *
   * "Project" drops out on a node with no descendant projects, where it can
   * only ever produce one group. ⚠️ It stays if it is the CURRENT value —
   * removing the selected option from a `<select>` renders it blank, and a
   * saved view that grouped by project is honoured rather than silently
   * rewritten when you open it on a leaf. So you can switch away from it and
   * not back, which is exactly the availability the tree describes.
   */
  const axisOffered = (option: GroupBy, current: GroupBy): boolean =>
    option !== "project" || spansProjects || current === "project";
  // The search box is held locally and pushed up on a delay. Refetching on
  // every keystroke turns a five-letter word into five round trips, and the
  // board flickering through four wrong answers reads as a broken filter.
  const [draft, setDraft] = useState(filters.q);
  const [naming, setNaming] = useState(false);
  const [viewName, setViewName] = useState("");
  // WS-27x — the shown-fields picker's popover.
  const [pickingFields, setPickingFields] = useState(false);
  // WS-27ae — the export is a round trip that can REFUSE (a filter wider than
  // the server's row cap answers 422 rather than handing back a partial file),
  // so the button has to be able to say "working" and cannot be a link.
  const [exporting, setExporting] = useState(false);

  useEffect(() => setDraft(filters.q), [filters.q]);

  useEffect(() => {
    if (draft === filters.q) return;
    const timer = setTimeout(() => onFilters({ ...filters, q: draft }), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const set = (patch: Partial<Filters>) => onFilters({ ...filters, ...patch });

  /**
   * A filtered-to address that is neither "me" nor anyone with work here.
   *
   * A saved view can name somebody whose tasks have all closed, or who has
   * left. The `<select>` would then have no matching option, render BLANK, and
   * read as "Anyone" while the filter is still applied — the worst outcome, a
   * control lying about the state it is in. Given its own option instead.
   */
  const chosen = filters.assignee.trim();
  const orphanAssignee =
    chosen &&
    chosen.toLowerCase() !== me.toLowerCase() &&
    !people.some((who) => who.toLowerCase() === chosen.toLowerCase())
      ? chosen
      : null;

  // Resolved from the list rather than trusted: `activeViewId` outlives a
  // project switch and a delete, and a chip lit for a view that is no longer
  // in `views` would offer to update something that is not there.
  const activeView = views.find((view) => view.id === activeViewId) ?? null;
  const drift = activeView
    ? viewDivergence({ filters, groupBy, lanes, shownFields }, activeView.config)
    : { dirty: false, changed: [] as never[] };

  return (
    <div className="border-b border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[10rem] flex-1">
          <Input
            icon="Search"
            inputSize="sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Search titles and descriptions…"
            aria-label="Search tasks"
          />
        </div>

        <select
          aria-label="Status"
          className={SELECT}
          value={filters.statusCategory}
          onChange={(e) => set({ statusCategory: e.target.value })}
        >
          {CATEGORIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>

        {/* ── Assignee (WS-27af) ───────────────────────────────────────────
            One axis, one control. This was two toggle buttons — "Mine" and
            "Unassigned" — which offered the viewer exactly two of the people
            who might hold work, and could not express "Priya's". The server
            has always accepted any address here; only the UI was narrow.

            A `Select`, matching the Status control beside it, because that is
            what this is: pick one value on one axis. It is deliberately NOT
            the directory-backed `AssigneePicker` — that control exists to
            ASSIGN, and its warnings ("away until the 20th", "more committed
            than contracted") are advice about giving someone work, which means
            nothing when you are reading it.

            "Mine" survives as an OPTION rather than a button. It still writes
            the viewer's own address rather than a server-side flag, so a saved
            view carries whose work it meant instead of resolving to whoever
            opens it later. */}
        <div className="w-40">
          <Select
            inputSize="sm"
            aria-label="Assignee"
            value={filters.unassigned ? UNSET : filters.assignee}
            onChange={(e) => {
              const picked = e.target.value;
              // `unassigned` is its own server flag, so the two are set as a
              // pair — every path here writes both and they cannot drift into
              // "nobody's tasks, assigned to Priya".
              set(
                picked === UNSET
                  ? { assignee: "", unassigned: true }
                  : { assignee: picked, unassigned: false }
              );
            }}
          >
            <option value="">Anyone</option>
            {me ? <option value={me}>Me</option> : null}
            <option value={UNSET}>Unassigned</option>
            {people.length > 0 ? (
              <optgroup label="Assignees">
                {people.map((who) => (
                  <option key={who} value={who}>
                    {personLabel(who)}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {/* A saved view can name somebody who holds nothing right now.
                Without this the select would render blank and silently read
                as "Anyone" while still filtering to them. */}
            {orphanAssignee ? (
              <option value={orphanAssignee}>{personLabel(orphanAssignee)}</option>
            ) : null}
          </Select>
        </div>
        <Button
          variant={filters.overdue ? "primary" : "secondary"}
          size="sm"
          aria-pressed={filters.overdue}
          onClick={() => set({ overdue: !filters.overdue })}
        >
          Overdue
        </Button>

        {/* Both axes are offered only where the canvas draws them. Calendar
            and Timeline honour neither, and only the board has a second
            axis — see `honoursGroupBy` / `honoursLanes`. Hiding the control
            never clears the value: switching away and back keeps the
            grouping, and a saved view carries both axes whichever canvas
            saved it. */}
        {honoursGroupBy(mode) ? (
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Group by
            <select
              aria-label="Group by"
              className={SELECT}
              value={groupBy}
              onChange={(e) => onGroupBy(e.target.value as GroupBy)}
            >
              {GROUP_OPTIONS.filter((option) =>
                axisOffered(option, groupBy)
              ).map((option) => (
                <option key={option} value={option}>
                  {GROUP_LABELS[option]}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {/* WS-27y — the board's second axis. The main axis is withheld from
            the options: a board laned by its own columns means nothing, and
            `fromConfig` would normalise it away anyway. */}
        {honoursLanes(mode) ? (
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Lanes
            <select
              aria-label="Sub-group by (swimlanes)"
              className={SELECT}
              value={subGroupBy === groupBy ? "none" : subGroupBy}
              onChange={(e) => onSubGroupBy(e.target.value as GroupBy)}
            >
              {GROUP_OPTIONS.filter(
                (option) =>
                  (option === "none" || option !== groupBy) &&
                  axisOffered(option, subGroupBy)
              ).map((option) => (
                <option key={option} value={option}>
                  {option === "none" ? "No lanes" : GROUP_LABELS[option]}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {/* WS-27x — which fields this view shows. ONE set feeding two
            consumers: the table's columns and every card's chip row, so
            hiding a field here silences it everywhere at once. */}
        <div className="relative">
          <Button
            variant={sameFieldSet(shownFields, DEFAULT_SHOWN) ? "secondary" : "primary"}
            size="sm"
            icon="Columns3"
            aria-expanded={pickingFields}
            onClick={() => setPickingFields((open) => !open)}
          >
            Fields
          </Button>
          {pickingFields ? (
            <div
              className="absolute right-0 z-20 mt-1 w-52 rounded-lg border border-border bg-popover p-2 shadow-md"
              onKeyDown={(e) => {
                if (e.key === "Escape") setPickingFields(false);
              }}
            >
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[11px] font-medium text-foreground">
                  Shown fields
                </span>
                <Button
                  variant="text"
                  size="sm"
                  disabled={sameFieldSet(shownFields, DEFAULT_SHOWN)}
                  onClick={() => onShownFields([...DEFAULT_SHOWN])}
                >
                  Reset
                </Button>
              </div>
              <div className="max-h-64 space-y-0.5 overflow-y-auto">
                {[
                  ...FIELD_KEYS.map((key) => ({ key, label: FIELD_LABELS[key] })),
                  ...fields.map((def) => ({
                    key: customFieldKey(def),
                    label: def.name,
                  })),
                ].map(({ key, label }) => (
                  <label
                    key={key}
                    className="flex items-center gap-2 rounded px-1 py-0.5 text-xs text-foreground hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={shownFields.includes(key)}
                      onChange={() => onShownFields(toggleField(shownFields, key))}
                      aria-label={`Show ${label}`}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        {/* WS-27ae — what you export is what you are looking at: these
            filters, those columns. The house `Button`, never an `<a download>`
            dressed as one: this is a fetch whose refusal has to be shown. */}
        <Button
          variant="secondary"
          size="sm"
          icon="Download"
          disabled={exporting}
          title="Export these filters and columns as a CSV"
          onClick={async () => {
            setExporting(true);
            try {
              await onExport();
            } finally {
              setExporting(false);
            }
          }}
        >
          {exporting ? "Exporting…" : "Export"}
        </Button>

        {isFiltered(filters) ? (
          <Button
            variant="ghost"
            size="sm"
            icon="X"
            onClick={() => onFilters(EMPTY_FILTERS)}
          >
            Clear
          </Button>
        ) : null}
      </div>

      {/* WS-27m — tag chips, only when the project has any. A row of controls
          for a feature nobody uses is a row of noise, and a project that never
          tags anything should not look like it forgot to. */}
      {tags.length ? (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {byUsage(tags)
            .slice(0, 12)
            .map((tag) => {
              const on = filters.tags.includes(tag.name);
              return (
                <button
                  key={tag.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    onFilters({
                      ...filters,
                      tags: on
                        ? filters.tags.filter((name) => name !== tag.name)
                        : [...filters.tags, tag.name],
                    })
                  }
                  className={`rounded-md px-1.5 py-0.5 text-[11px] ${
                    on
                      ? "bg-accent text-accent-foreground"
                      : chipClass(tag.color)
                  }`}
                >
                  {tag.name}
                  <span className="ml-1 opacity-70">{tag.task_count ?? 0}</span>
                </button>
              );
            })}
        </div>
      ) : null}

      {/* Saved views. Chips rather than a dropdown: the point of saving one is
          that it is one click away, and a menu puts it two. */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {views.map((view) => (
          <span key={view.id} className="inline-flex items-center">
            <button
              type="button"
              onClick={() => onApplyView(view)}
              // The house's active token (AGENTS.md rule 6). It was
              // `bg-accent text-accent-foreground`, which resolves to a
              // different colour per theme than every other selected thing in
              // the app — so the applied view read as "selected" in one theme
              // and as "highlighted" in the next.
              className={`rounded-l-md px-2 py-1 text-xs ${
                view.id === activeViewId
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {view.name}
              {/* WS-27ab — the edited marker. A dot rather than a word: the
                  row below says what changed, and this only has to answer
                  "is this chip still telling the truth". */}
              {view.id === activeViewId && drift.dirty ? (
                <span
                  className="ml-1 align-middle text-[10px]"
                  aria-label="edited since it was saved"
                  title="Edited since it was saved"
                >
                  ●
                </span>
              ) : null}
            </button>
            <button
              type="button"
              aria-label={`Delete view ${view.name}`}
              title={`Delete view ${view.name}`}
              onClick={() => onDeleteView(view)}
              className="rounded-r-md px-1 py-1 text-muted-foreground hover:bg-muted"
            >
              <Icon name="X" size={11} />
            </button>
          </span>
        ))}

        {naming ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              const name = viewName.trim();
              if (!name) return;
              onSaveView(name);
              setViewName("");
              setNaming(false);
            }}
          >
            <Input
              autoFocus
              inputSize="sm"
              value={viewName}
              onChange={(e) => setViewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setNaming(false);
              }}
              placeholder="View name"
              aria-label="View name"
            />
            <Button type="submit" size="sm">
              Save
            </Button>
          </form>
        ) : canSave ? (
          <Button
            variant="text"
            size="sm"
            icon="Bookmark"
            disabled={
              !isFiltered(filters) &&
              groupBy === "status" &&
              subGroupBy === "none" &&
              sameFieldSet(shownFields, DEFAULT_SHOWN)
            }
            title={
              !isFiltered(filters) &&
              groupBy === "status" &&
              subGroupBy === "none" &&
              sameFieldSet(shownFields, DEFAULT_SHOWN)
                ? "Filter, regroup or change the fields first — an untouched view is the board"
                : "Save these filters as a view"
            }
            onClick={() => setNaming(true)}
          >
            Save view
          </Button>
        ) : null}

        {isFiltered(filters) ? (
          <Badge tone="primary" icon="Filter">
            filtered
          </Badge>
        ) : null}
      </div>

      {/* WS-27ab — the dirty-view row. Three ways out and no fourth:
          keep the change on this view, keep it as a new one, or throw it away.
          Reset re-applies the SAVED config through the same `onApplyView` the
          chip uses — one path back, so "reset" and "click the chip again"
          cannot come to mean different things. */}
      {activeView && drift.dirty ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted px-2 py-1.5">
          <Icon name="PencilLine" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <p className="min-w-0 flex-1 text-[11px] text-foreground">
            <span className="font-medium">{activeView.name}</span> has unsaved
            changes to its {describeDivergence(drift.changed)}.
          </p>
          <Button size="sm" icon="Save" onClick={() => onUpdateView(activeView)}>
            Update view
          </Button>
          <Button
            variant="secondary"
            size="sm"
            // `Plus`, not the `Bookmark` on the Save-view button beside it:
            // Bookmark has no entry in `icon-data/registry.json`, so it draws
            // the Lucide glyph under Fluent and Material. Fixing the existing
            // one is a registry ticket; adding a second is a choice.
            icon="Plus"
            disabled={!canSave}
            title={
              canSave
                ? "Save these settings as a second view"
                : "Saving needs a project selected"
            }
            onClick={() => {
              setViewName(`${activeView.name} copy`);
              setNaming(true);
            }}
          >
            Save as new
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon="RotateCcw"
            title={`Go back to ${activeView.name} as it was saved`}
            onClick={() => onApplyView(activeView)}
          >
            Reset
          </Button>
        </div>
      ) : null}
    </div>
  );
}
