"use client";

/**
 * The shared list — one component, four entities (specs/crm_app.md §5
 * surface 2, §4's list contract).
 *
 * Four list components is how a shared contract stops being shared: one grows
 * a different page cap, another a different sort key, and the endpoint's
 * single contract becomes four. The request is built once in ../lib/filters.ts.
 *
 * ⚠️ **The columns are declared in ../lib/columns.ts, not here** (WS-26i-export).
 * The CSV export writes the same set in the same order, and a file whose
 * columns differ from the screen's is a second opinion about the same rows —
 * so the vocabulary is one declaration, read by a Python fence
 * (`tests/unit/test_crm_export.py`). What stays here is RENDERING, which is
 * JSX and per-theme and could not live in a `.ts` file anyway.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { relativeTime } from "@/lib/taskCard";
import { useState } from "react";
import {
  MAX_COLUMNS,
  chosenDefs,
  listCell,
  pickable,
  readColumns,
  toggleColumn,
  writeColumns,
  type FieldDef,
} from "../lib/fields";
import FieldValue from "./FieldValue";
import { stageAccent } from "../lib/board";
import { COLUMNS } from "../lib/columns";
import { SORTS } from "../lib/filters";
import { money, shortDate, shortEmail } from "../lib/format";
import type { EntitySlug, Status } from "../lib/types";

type Row = Record<string, unknown>;

/** What a renderer may read beside its row. */
type CellContext = { statuses: ReadonlyArray<Status> };

function text(row: Row, key: string): string {
  const value = row[key];
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

/**
 * How a cell draws, when it is not just its own text.
 *
 * Keyed by column key rather than by entity+key because every one of these is
 * the same decision wherever it appears: an owner is always an initial, an
 * amount is always money. A key with no entry falls through to `text`, which
 * is what most columns want — so adding a plain column to `columns.ts` needs
 * no edit here, and adding a formatted one fails visibly (raw value on screen)
 * rather than silently.
 */
const RENDERERS: Record<string, (row: Row, ctx: CellContext) => React.ReactNode> = {
  amount: (r) => money(r.amount as number | null, (r.currency as string) || "INR"),
  expected_close_date: (r) => shortDate(r.expected_close_date as string | null),
  owner_email: (r) => shortEmail(r.owner_email as string | null),
  first_name: (r) => `${text(r, "first_name")} ${r.last_name ?? ""}`.trim(),
  // CRM-X1: the stage by name, with the dot of `stageAccent`, the one stage
  // colour of the CRM. A stage the vocabulary does not hold draws a muted dot.
  // The list does not know the mode, so it passes null and reads the stored
  // colour (CRM-C2).
  status_name: (r, ctx) => {
    if (!r.status_name) return "—";
    const status = ctx.statuses.find((s) => s.id === r.status_id);
    return (
      <span className="inline-flex items-center gap-1.5">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${status ? stageAccent(status, null).dot : "bg-muted-foreground"}`}
        />
        {String(r.status_name)}
      </span>
    );
  },
  last_activity_at: (r) => relativeTime(r.last_activity_at as string | null) || "—",
  converted_deal_id: (r) =>
    r.converted_deal_id ? (
      <span className="rounded-full bg-success/10 px-2 py-0.5 text-[10px] text-success">
        converted
      </span>
    ) : null,
};

function cell(key: string, row: Row, ctx: CellContext): React.ReactNode {
  return (RENDERERS[key] ?? ((r: Row) => text(r, key)))(row, ctx);
}

export default function RecordList({
  entity,
  columns: columnsProp,
  sortKeys,
  rows,
  total,
  loading,
  sort,
  direction,
  onSort,
  onOpen,
  fieldDefs = null,
  columnKeys,
  onColumnsChange,
  statuses = [],
  onLoadMore,
  loadingMore = false,
  hasMore = true,
}: {
  /** The collection, or a generic module (`module:Products`, CRM-Q6b). */
  entity: EntitySlug | `module:${string}`;
  /** The columns of a generic module. The four entities read `COLUMNS`. */
  columns?: (typeof COLUMNS)[EntitySlug];
  /** The sort keys of a generic module. The four entities read `SORTS`. */
  sortKeys?: readonly string[];
  rows: Row[];
  total: number;
  loading: boolean;
  sort: string | null;
  direction: "asc" | "desc";
  onSort: (key: string) => void;
  onOpen: (id: string) => void;
  /** What each custom field means (CRM-Q4). The member may show up to five as columns. */
  fieldDefs?: FieldDef[] | null;
  /**
   * The chosen custom columns of the saved view in effect (CRM-Q8). When it is
   * given, the view owns the choice and the browser's choice is left alone.
   * When it is absent, the choice is per entity and lives in this browser.
   */
  columnKeys?: string[];
  onColumnsChange?: (keys: string[]) => void;
  /** The stages or statuses of this entity, for the dot of the Stage column. */
  statuses?: ReadonlyArray<Status>;
  /** CRM-X1: read the next page of the same view. */
  onLoadMore?: () => void;
  /** The next page is being read. */
  loadingMore?: boolean;
  /** False once a page came back short or empty: the list has ended. */
  hasMore?: boolean;
}) {
  const columns = columnsProp ?? COLUMNS[entity as EntitySlug];
  // The choice is per entity and lives in this browser. The state re-seats
  // while rendering when the entity changes, so no frame shows the old list's choice.
  const [picked, setPicked] = useState(() => ({ entity, keys: readColumns(entity) }));
  const [choosing, setChoosing] = useState(false);
  let chosen = picked.keys;
  if (picked.entity !== entity) {
    chosen = readColumns(entity);
    setPicked({ entity, keys: chosen });
  }
  const owned = columnKeys !== undefined;
  if (owned) chosen = columnKeys;
  const available = pickable(fieldDefs);
  const extra = chosenDefs(chosen, fieldDefs);
  function toggle(key: string) {
    const keys = toggleColumn(chosen, key);
    if (owned) {
      onColumnsChange?.(keys);
      return;
    }
    writeColumns(entity, keys);
    setPicked({ entity, keys });
  }
  const sortable = new Set(sortKeys ?? SORTS[entity as EntitySlug].map((s) => s.key));

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {available.length > 0 && (
        <div className="flex shrink-0 justify-end border-b border-border px-4 py-1.5">
          <Button variant="ghost" size="sm" onClick={() => setChoosing(true)}>
            Columns
          </Button>
        </div>
      )}
      {choosing && (
        <ColumnChooser
          available={available}
          chosen={chosen}
          onToggle={toggle}
          onClose={() => setChoosing(false)}
        />
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-background">
            <tr className="border-b border-border">
              {columns.map((column) => (
                <th
                  key={column.key}
                  className="px-4 py-2 text-left font-medium text-muted-foreground"
                >
                  {column.sort && sortable.has(column.sort) ? (
                    <button
                      onClick={() => onSort(column.sort!)}
                      className="inline-flex items-center gap-1 hover:text-foreground tech-transition"
                    >
                      {column.label}
                      {sort === column.sort &&
                        (direction === "asc" ? (
                          <Icon name="ArrowUp" className="w-3 h-3" />
                        ) : (
                          <Icon name="ArrowDown" className="w-3 h-3" />
                        ))}
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              ))}
              {extra.map((def) => (
                <th
                  key={`custom:${def.key}`}
                  className="px-4 py-2 text-left font-medium text-muted-foreground"
                >
                  {def.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={String(row.id)}
                onClick={() => onOpen(String(row.id))}
                className="cursor-pointer border-b border-border/60 hover:bg-secondary/40 tech-transition"
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`px-4 py-2 text-muted-foreground ${column.className ?? ""}`}
                  >
                    {cell(column.key, row, { statuses })}
                  </td>
                ))}
                {extra.map((def) => (
                  <td key={`custom:${def.key}`} className="px-4 py-2 text-muted-foreground">
                    <FieldValue value={listCell(row, def)} compact />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length === 0 && (
          <p className="p-8 text-center text-xs text-muted-foreground">
            Nothing matches this view.
          </p>
        )}
        {!loading && onLoadMore && hasMore && rows.length > 0 && rows.length < total && (
          <div className="flex justify-center p-3">
            <Button variant="secondary" size="sm" loading={loadingMore} onClick={onLoadMore}>
              Load more
            </Button>
          </div>
        )}
      </div>
      <footer className="shrink-0 border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
        {loading ? "Loading…" : `${rows.length} of ${total}`}
      </footer>
    </div>
  );
}

function ColumnChooser({
  available,
  chosen,
  onToggle,
  onClose,
}: {
  available: FieldDef[];
  chosen: string[];
  onToggle: (key: string) => void;
  onClose: () => void;
}) {
  const full = chosen.length >= MAX_COLUMNS;
  return (
    <Modal
      open
      onClose={onClose}
      title="Columns"
      description={`Pick up to ${MAX_COLUMNS} fields to show in this list. ${chosen.length} of ${MAX_COLUMNS} chosen.`}
      size="sm"
      closeLabel="Close columns"
      className="max-h-[85vh]"
    >
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-4">
        {available.map((def) => {
          const on = chosen.includes(def.key);
          return (
            <li key={def.key}>
              <label className="flex items-center gap-2 text-xs text-foreground">
                <Checkbox
                  checked={on}
                  disabled={full && !on}
                  onChange={() => onToggle(def.key)}
                  aria-label={def.label}
                />
                {def.label}
              </label>
            </li>
          );
        })}
      </ul>
    </Modal>
  );
}
