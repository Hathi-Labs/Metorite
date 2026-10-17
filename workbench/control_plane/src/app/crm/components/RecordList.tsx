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
 *
 * The fill rank (WS-53 CRM-Q17a-2, `crm_platform.md` section 18.9.6):
 *
 * - The list hides each standard column of `empty_columns` (`shownColumns`).
 * - With no saved choice, the custom columns are the fields with the highest
 *   fill (`effectiveColumns`). A saved view or a member's choice wins, and a
 *   saved empty list stays empty.
 * - A cell does not wrap. Long text ends in an ellipsis, and the cell's title
 *   holds the full text.
 * - An owner who is not a member shows by the Zoho name, with "Zoho only".
 *
 * At phone width each row is a two-line card (WS-53 CRM-Q17b item 7,
 * `RecordCards`): the name, then the next columns on one muted line. The
 * whole card is one button, so the keyboard opens the record too.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useViewMode } from "@/components/ViewModeProvider";
import { relativeTime } from "@/lib/taskCard";
import { useId, useState } from "react";
import {
  MAX_COLUMNS,
  chosenDefs,
  columnGroups,
  effectiveColumns,
  fieldMatches,
  fieldText,
  fillLabel,
  listCell,
  pickable,
  readColumns,
  toggleColumn,
  writeColumns,
  zohoOwnerName,
  zohoOwnerText,
  type FieldDef,
  type FieldGroup,
} from "../lib/fields";
import { useFieldLayout } from "../lib/useCrmMode";
import FieldValue from "./FieldValue";
import { stageAccent } from "../lib/board";
import type { CrmMode } from "../lib/connect";
import { useCrmModeValue } from "../lib/modeContext";
import { COLUMNS, shownColumns } from "../lib/columns";
import { SORTS } from "../lib/filters";
import { money, shortDate, shortEmail } from "../lib/format";
import type { EntitySlug, Status } from "../lib/types";

type Row = Record<string, unknown>;

/** What a renderer may read beside its row. */
type CellContext = { statuses: ReadonlyArray<Status>; mode: CrmMode };

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
  // CRM-Q17a-2 item 4: an owner who is not a member, by the Zoho name.
  owner_email: (r) => {
    const zoho = zohoOwnerName(r);
    return zoho ? zohoOwnerText(zoho) : shortEmail(r.owner_email as string | null);
  },
  first_name: (r) => `${text(r, "first_name")} ${r.last_name ?? ""}`.trim(),
  // CRM-X1: the stage by name, with the dot of `stageAccent`, the one stage
  // colour of the CRM. A stage the vocabulary does not hold draws a muted dot.
  // The mode comes from the page (`lib/modeContext.ts`, CRM-C2), so a mirrored
  // won stage draws the same green dot here as on the board.
  status_name: (r, ctx) => {
    if (!r.status_name) return "—";
    const status = ctx.statuses.find((s) => s.id === r.status_id);
    return (
      <span className="inline-flex items-center gap-1.5">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${status ? stageAccent(status, ctx.mode).dot : "bg-muted-foreground"}`}
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

/**
 * The full text of a cell, for its title (CRM-Q17a-2 item 3). A cell that
 * draws markup names its text here. An empty cell has no title.
 */
function cellTitle(key: string, row: Row, node: React.ReactNode): string | undefined {
  if (typeof node === "string") return node && node !== "—" ? node : undefined;
  if (key === "status_name" && row.status_name) return String(row.status_name);
  return undefined;
}

/** A cell's content: one line, cut with an ellipsis (CRM-Q17a-2 item 3). */
export const ONE_LINE = "block max-w-xs truncate";

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
  emptyNote,
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
   * `null` is a view that saved no choice: the fill rank chooses (CRM-Q17a-2).
   */
  columnKeys?: string[] | null;
  onColumnsChange?: (keys: string[]) => void;
  /** The stages or statuses of this entity, for the dot of the Stage column. */
  statuses?: ReadonlyArray<Status>;
  /** CRM-X1: read the next page of the same view. */
  onLoadMore?: () => void;
  /** The next page is being read. */
  loadingMore?: boolean;
  /** False once a page came back short or empty: the list has ended. */
  hasMore?: boolean;
  /**
   * CRM-Q20-2 item 5: why the list is empty, with no filter on. A member
   * reads it in place of "Nothing matches this view.", which is for a filter.
   */
  emptyNote?: string;
}) {
  // CRM-C2: the Stage dot reads the mode, so it agrees with the board.
  const mode = useCrmModeValue();
  // CRM-Q17b item 7: at phone width each row is a two-line card.
  const { isMobile } = useViewMode();
  // The choice is per entity and lives in this browser. The state re-seats
  // while rendering when the entity changes, so no frame shows the old list's choice.
  // `null` is "nothing saved", and `[]` is "saved empty" (CRM-Q17a-2 item 2).
  const [picked, setPicked] = useState(() => ({ entity, keys: readColumns(entity) }));
  const [choosing, setChoosing] = useState(false);
  let saved = picked.keys;
  if (picked.entity !== entity) {
    saved = readColumns(entity);
    setPicked({ entity, keys: saved });
  }
  const owned = columnKeys !== undefined;
  if (owned) saved = columnKeys;
  // CRM-C4b: with the field studio on, the Columns control reads the layout
  // and draws a heading for each section. A generic module has no layout.
  const { layout } = useFieldLayout(entity.startsWith("module:") ? null : (entity as EntitySlug));
  const studio = layout?.studio === true ? layout : null;
  const defs = studio ? studio.fields : fieldDefs;
  // CRM-Q17a-2 item 1: a standard column that no record fills is not drawn.
  const columns = shownColumns(columnsProp ?? COLUMNS[entity as EntitySlug], layout?.empty_columns);
  // CRM-Q17a-2 item 2: with no saved choice, the fields with the highest fill.
  const chosen = effectiveColumns(saved, defs);
  const available = pickable(defs);
  const groups: FieldGroup[] = studio
    ? columnGroups(studio)
    : [{ section: null, fields: available }];
  const extra = chosenDefs(chosen, defs);
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
          groups={groups}
          chosen={chosen}
          onToggle={toggle}
          onClose={() => setChoosing(false)}
        />
      )}
      <div className="flex-1 overflow-auto">
        {isMobile ? (
          <RecordCards
            rows={rows}
            columns={columns}
            extra={extra}
            ctx={{ statuses, mode }}
            onOpen={onOpen}
          />
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-background">
              <tr className="border-b border-border">
                {columns.map((column) => (
                  <th
                    key={column.key}
                    className="whitespace-nowrap px-4 py-2 text-left font-medium text-muted-foreground"
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
                    className="whitespace-nowrap px-4 py-2 text-left font-medium text-muted-foreground"
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
                  {columns.map((column, index) => {
                    const content = cell(column.key, row, { statuses, mode });
                    const title = cellTitle(column.key, row, content);
                    return (
                      <td
                        key={column.key}
                        className={`whitespace-nowrap px-4 py-2 text-muted-foreground ${column.className ?? ""}`}
                      >
                        {index === 0 ? (
                          // The row opens on a click, and Tab cannot reach a row. So
                          // the first cell is a real button: the keyboard opens the
                          // record here (DESIGN_SYSTEM §9, fence `crmRowKeyboard.test.ts`).
                          <button
                            type="button"
                            title={title}
                            onClick={(event) => {
                              event.stopPropagation();
                              onOpen(String(row.id));
                            }}
                            className={`cc-link rounded-sm text-left ${ONE_LINE}`}
                          >
                            {content}
                          </button>
                        ) : (
                          <span className={ONE_LINE} title={title}>
                            {content}
                          </span>
                        )}
                      </td>
                    );
                  })}
                  {extra.map((def) => {
                    const value = listCell(row, def);
                    return (
                      <td
                        key={`custom:${def.key}`}
                        className="whitespace-nowrap px-4 py-2 text-muted-foreground"
                      >
                        <span
                          className={ONE_LINE}
                          title={value.kind === "empty" ? undefined : fieldText(value)}
                        >
                          <FieldValue value={value} compact />
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!loading && rows.length === 0 && (
          <p className="p-8 text-center text-xs text-muted-foreground">
            {emptyNote ?? "Nothing matches this view."}
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

/** The most cells on the second line of a phone card, after the name. */
export const CARD_DETAILS = 3;

/** A cell that draws nothing: no value, or the dash of an empty value. */
function blank(node: React.ReactNode): boolean {
  return node === null || node === undefined || node === "" || node === "—";
}

/**
 * The rows at phone width (CRM-Q17b item 7): a two-line card for each row.
 * Line 1 is the first column, the name. Line 2 holds the next cells that
 * have a value, at most `CARD_DETAILS`, with the custom columns last. Each
 * line is cut with an ellipsis, so a card never grows past two lines.
 */
export function RecordCards({
  rows,
  columns,
  extra,
  ctx,
  onOpen,
}: {
  rows: Row[];
  columns: ReadonlyArray<{ key: string; label: string }>;
  extra: FieldDef[];
  ctx: CellContext;
  onOpen: (id: string) => void;
}) {
  return (
    <ul className="divide-y divide-border/60" aria-label="Records">
      {rows.map((row) => {
        const [first, ...rest] = columns;
        const name = first ? cell(first.key, row, ctx) : String(row.id);
        const details: Array<{ key: string; label: string; node: React.ReactNode }> = [];
        for (const column of rest) {
          const node = cell(column.key, row, ctx);
          if (!blank(node)) details.push({ key: column.key, label: column.label, node });
        }
        for (const def of extra) {
          const value = listCell(row, def);
          if (value.kind !== "empty") {
            details.push({ key: `custom:${def.key}`, label: def.label, node: fieldText(value) });
          }
        }
        return (
          <li key={String(row.id)}>
            <button
              type="button"
              onClick={() => onOpen(String(row.id))}
              className="flex w-full min-w-0 flex-col gap-0.5 px-4 py-2.5 text-left hover:bg-secondary/40 tech-transition"
            >
              <span className="block w-full truncate text-sm font-medium text-foreground">{name}</span>
              <span className="flex w-full min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap text-[11px] text-muted-foreground">
                {details.length === 0 ? (
                  <span>—</span>
                ) : (
                  details.slice(0, CARD_DETAILS).map((d, i) => (
                    <span key={d.key} className="inline-flex min-w-0 items-center gap-1.5 truncate" title={d.label}>
                      {i > 0 ? <span aria-hidden="true">·</span> : null}
                      <span className="truncate">{d.node}</span>
                    </span>
                  ))
                )}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ColumnChooser({
  groups,
  chosen,
  onToggle,
  onClose,
}: {
  groups: FieldGroup[];
  chosen: string[];
  onToggle: (key: string) => void;
  onClose: () => void;
}) {
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
      <ColumnChoices groups={groups} chosen={chosen} onToggle={onToggle} />
    </Modal>
  );
}

/** The heading of the chosen fields at the top of the Columns control. */
export const CHOSEN_HEADING = "Shown in the list";

/** Why a box of the Columns control is disabled. */
export const FULL_TIP = `The list shows ${MAX_COLUMNS} fields at most. Clear one first.`;

/**
 * The list of the Columns control. A group with a section draws its label
 * as a heading (CRM-C4b). A group with no section draws no heading, so the
 * list of today, one group with no section, draws as before.
 *
 * CRM-Q17b item 6: a search box finds a field by its label. The chosen
 * fields come first, under their own heading. Each field shows "% filled"
 * when the gateway sent a fill number. A box that the limit disables looks
 * disabled: a muted label, the not-allowed cursor and a tip.
 */
export function ColumnChoices({
  groups,
  chosen,
  onToggle,
}: {
  groups: FieldGroup[];
  chosen: string[];
  onToggle: (key: string) => void;
}) {
  const full = chosen.length >= MAX_COLUMNS;
  const baseId = useId();
  const [query, setQuery] = useState("");
  const all = groups.flatMap((group) => group.fields);
  const picked = chosen
    .map((key) => all.find((def) => def.key === key))
    .filter((def): def is FieldDef => def !== undefined && fieldMatches(def, query));
  const rest = groups
    .map((group) => ({
      ...group,
      fields: group.fields.filter((def) => !chosen.includes(def.key) && fieldMatches(def, query)),
    }))
    .filter((group) => group.fields.length > 0);
  const item = (def: FieldDef) => {
    const on = chosen.includes(def.key);
    const off = full && !on;
    const filled = fillLabel(def);
    return (
      <li key={def.key}>
        <label
          className={`flex items-center gap-2 text-xs ${off ? "cursor-not-allowed text-muted-foreground" : "text-foreground"}`}
          title={off ? FULL_TIP : undefined}
        >
          <Checkbox
            checked={on}
            disabled={off}
            className="disabled:bg-muted"
            onChange={() => onToggle(def.key)}
            aria-label={def.label}
          />
          <span className="min-w-0 flex-1 truncate">{def.label}</span>
          {filled ? (
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{filled}</span>
          ) : null}
        </label>
      </li>
    );
  };
  const chosenId = `${baseId}-chosen`;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-4 pt-4">
        <Input
          icon="Search"
          inputSize="sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a field"
          aria-label="Find a field"
        />
      </div>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-4">
        {picked.length > 0 ? (
          <li key="chosen" role="group" aria-labelledby={chosenId} className="pb-2">
            <span
              id={chosenId}
              className="block text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
            >
              {CHOSEN_HEADING}
            </span>
            <ul className="mt-1 space-y-1">{picked.map(item)}</ul>
          </li>
        ) : null}
        {picked.length === 0 && rest.length === 0 ? (
          <li className="py-4 text-center text-xs text-muted-foreground">No field matches.</li>
        ) : null}
        {rest.flatMap((group) => {
          if (!group.section) return group.fields.map(item);
          // A section is a group with its label as the name (review note).
          const headingId = `${baseId}-${group.section.id}`;
          return [
            <li
              key={`section:${group.section.id}`}
              role="group"
              aria-labelledby={headingId}
              className="pt-2 first:pt-0"
            >
              <span
                id={headingId}
                className="block text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
              >
                {group.section.label}
              </span>
              <ul className="mt-1 space-y-1">{group.fields.map(item)}</ul>
            </li>,
          ];
        })}
      </ul>
    </div>
  );
}
