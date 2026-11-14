"use client";

/**
 * The sheet of line items on the record page (STU-D17a, `ai_studio.md` D17 and C13).
 *
 * A parent object with a `lines` field shows its lines here, in the Lines tab. A cell edits into a
 * LOCAL BUFFER (`lib/studio/sheetModel.ts`), and no cell writes. A paste of rows from Excel or
 * Google Sheets fills the buffer too (`sheetPaste.ts`). One Save sends ONE `object:<parent>.update`
 * through the door (`saveSheet`), so the record and its lines save together, all or none.
 *
 * The sheet judges no rule. The door does. A refusal keeps every edit. A line refusal shows in the
 * State column of its row, and a rule of the parent (such as "the lines sum to 100 %") shows as one
 * alert over the sheet. The grid is the shared `DataGrid` with its `GridEdit` seam, and the cells are
 * the shared editors (`StudioCellEditors`), so the sheet is a table beside the Table block, and a
 * phone gets stacked rows. A viewer gets a read-only sheet: no Save, no paste, no new row.
 */
import { useMemo, useRef, useState } from "react";

import { DataGrid, type GridEdit } from "@/components/genUITemplates";
import RecordsStatus from "@/components/studio/RecordsStatus";
import { cellEditor } from "@/components/studio/StudioCellEditors";
import Button from "@/components/ui/Button";
import { labelOf } from "@/lib/studio/blocks";
import { type Row, type StudioDataDoor, gridData, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { fieldSpecs } from "@/lib/studio/packagePreview";
import { type LinesField, relatedArgs } from "@/lib/studio/recordPageModel";
import { type DeclaredAction, type ObjectRow, verbMap } from "@/lib/studio/realDoor";
import {
  EMPTY_BUFFER, MAX_SHEET_LINES, STATE_KEY, type SheetBuffer, addRow, isDirty, rollupTargets, rowState, saveCount,
  saveSheet, setCell, sheetColumns, syncStored, valueOf,
} from "@/lib/studio/sheetModel";
import { type PasteFocus, pasteInto } from "@/lib/studio/sheetPaste";
import { pressOnce } from "@/lib/studio/TypedActionButton";
import { useLeaveGuard } from "@/lib/studio/useLeaveGuard";
import { useCachedResource } from "@/lib/useCachedResource";

export interface StudioSheetProps {
  /** The parent object, which holds the lines field. */
  parent: ObjectRow;
  lines: LinesField;
  /** The line object. */
  child: ObjectRow;
  recordId: string;
  door: StudioDataDoor;
  actions: readonly DeclaredAction[];
  /** The owner may write. A viewer reads only (J2a). */
  owner: boolean;
  /** The parent record as it is stored, or undefined while it loads. It holds the roll-ups. */
  record?: Row;
}

const NOTE = "text-xs text-muted-foreground";
const STATE_FIELD: FieldSpec = { key: STATE_KEY, type: "text", label: "State" };
const NEVER = { current: false };

const STATE_WORDS = { new: "New, not saved", changed: "Changed, not saved", saved: "Saved", "": "" } as const;

export default function StudioSheet({ parent, lines, child, recordId, door, actions, owner, record }: StudioSheetProps) {
  const verbs = verbMap(actions);
  const listAction = `object:${child.name}.list`;
  const canList = verbs[listAction] !== undefined;
  const updateDeclared = verbs[`object:${parent.name}.update`] !== undefined;
  const canWrite = owner && updateDeclared;

  const source = useMemo(
    () => ({ action: listAction, args: relatedArgs({ field: lines.back }, recordId) }), [listAction, lines.back, recordId]);
  const stored = useCachedResource<Row[]>(canList ? sourceKey(door, source) : null, () => door.read(source));

  const [buf, setBuf] = useState<SheetBuffer>(EMPTY_BUFFER);
  const [seen, setSeen] = useState<Row[] | undefined>(undefined);
  // Fresh stored rows join the buffer while the member's edits stay (set state in render: no flash).
  if (stored.data && stored.data !== seen) {
    setSeen(stored.data);
    setBuf((b) => syncStored(b, stored.data ?? []));
  }

  const [editing, setEditing] = useState<{ uid: string; key: string } | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [answer, setAnswer] = useState<Record<string, unknown> | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const focus = useRef<PasteFocus>({ uid: null, key: null });

  const specs = useMemo(() => fieldSpecs(child), [child]);
  const computed = useMemo(() => computedKeys(child.rules), [child.rules]);
  const columns = useMemo(() => sheetColumns(specs, lines.back, computed, canWrite), [specs, lines.back, computed, canWrite]);
  const totals = useMemo(() => {
    const parentSpecs = fieldSpecs(parent);
    return rollupTargets(parent.rules, child.name, lines.back).map((key) => ({
      key, label: labelOf(parentSpecs.find((f) => f.key === key) ?? { key, type: "number" }),
    }));
  }, [parent, child.name, lines.back]);

  const dirty = isDirty(buf);
  useLeaveGuard(dirty, NEVER);

  const rows = useMemo(() => buf.rows.map((row) => {
    const values: Record<string, unknown> = {};
    for (const c of columns) values[c.field.key] = valueOf(row, c.field.key);
    const refused = rowErrors[row.uid];
    return { id: row.uid, ...values, [STATE_KEY]: refused ? `Refused: ${refused}` : STATE_WORDS[rowState(row)] };
  }), [buf.rows, columns, rowErrors]);
  const grid = useMemo(
    () => gridData({ fields: [...columns.map((c) => c.field), STATE_FIELD] }, rows),
    [columns, rows]);

  const edit = useMemo<GridEdit | undefined>(() => {
    if (!canWrite) return undefined;
    const at = (index: number) => columns[index];
    return {
      can: (index) => !saving && at(index)?.editable === true,
      open: (uid, index) => {
        const c = at(index);
        if (!c || !c.editable) return;
        focus.current = { uid, key: c.field.key };
        setEditing({ uid, key: c.field.key });
      },
      pending: () => saving,
      editor: (uid, index) => {
        const c = at(index);
        const row = buf.rows.find((r) => r.uid === uid);
        if (!c || !row) return null;
        const current = valueOf(row, c.field.key);
        return cellEditor({
          field: c.field, current, busy: saving,
          editing: editing?.uid === uid && editing.key === c.field.key,
          onCommit: (draft) => {
            focus.current = { uid, key: c.field.key };
            const out = setCell(buf, uid, c.field, draft);
            setEditing(null);
            if (out.error) { setAlert(out.error); return; }
            setAlert(null);
            setBuf(out.buf);
          },
          onCancel: () => setEditing(null),
        });
      },
    };
  }, [canWrite, columns, buf, editing, saving]);

  const onPaste = (e: React.ClipboardEvent) => {
    if (!canWrite || saving) return;
    const text = e.clipboardData.getData("text/plain");
    const inField = (e.target as HTMLElement | null)?.closest?.("input, textarea") != null;
    // One value into an open cell is a normal paste. Many cells are rows of the sheet.
    if (inField && !/[\t\n\r]/.test(text.replace(/[\r\n]+$/, ""))) return;
    e.preventDefault();
    const out = pasteInto(buf, columns, text, focus.current);
    setEditing(null);
    if (!out.ok) { setAlert(out.message); setNotice(`Not pasted: ${out.message}`); return; }
    setAlert(null);
    setRowErrors({});
    setBuf(out.buf);
    setNotice(`Pasted ${out.rows} row${out.rows === 1 ? "" : "s"}. They are not saved yet.`);
  };

  const save = () => {
    if (saving) return;
    void pressOnce(busy, async () => {
      setSaving(true);
      setNotice("Saving");
      try {
        const out = await saveSheet(door, buf, { parent: parent.name, recordId, linesKey: lines.key, canWrite });
        if (out.kind === "saved") {
          setBuf(out.buf);
          setAnswer(out.answer);
          setAlert(null);
          setRowErrors({});
          setNotice("Saved");
        } else if (out.kind === "blocked") {
          setAlert(out.message);
          setNotice(`Not saved: ${out.message}`);
        } else {
          // A refusal keeps every edit: the buffer stays as it is.
          setAlert(`${out.refusal.alert ?? "A line was refused. Read its State."} Nothing was saved. Your edits are kept.`);
          setRowErrors(out.refusal.rows);
          setNotice("Not saved. Your edits are kept.");
        }
      } finally {
        setSaving(false);
      }
    });
  };

  if (!canList) {
    return <p role="status" className={NOTE}>This app does not declare a list of {child.label ?? child.name}, so it cannot show the lines.</p>;
  }
  const more = moreLine(stored.data);
  const count = saveCount(buf);
  const shownTotals = totals.map((t) => ({ ...t, value: (answer ?? record)?.[t.key] }));
  return (
    <section aria-label={lines.label} className="min-w-0 space-y-2" data-sheet={lines.key} onPaste={onPaste}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{lines.label}</h3>
        {shownTotals.some((t) => t.value !== undefined && t.value !== null) ? (
          <p className="text-xs text-foreground" data-sheet-total="">
            {shownTotals.filter((t) => t.value !== undefined && t.value !== null)
              .map((t) => `${t.label}: ${String(t.value)}`).join(" · ")}
          </p>
        ) : null}
      </div>
      {!owner ? (
        <p role="status" className={NOTE}>You can read these lines. Only the owner of the app can change them.</p>
      ) : !updateDeclared ? (
        <p role="status" className={NOTE}>
          This app does not declare an update of {parent.label ?? parent.name}, so the lines are read only.
        </p>
      ) : (
        <p className={NOTE}>Click a cell to edit it, or click a cell and paste rows from a spreadsheet. Nothing saves until you press Save.</p>
      )}
      {/* The slot keeps its height, so a refusal does not push the sheet down. A viewer has no refusal. */}
      {canWrite ? (
        <div data-refusal-slot="" className="min-h-7">
          {alert ? (
            <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {alert}
            </p>
          ) : null}
        </div>
      ) : null}
      <p className="sr-only" aria-live="polite">{notice}</p>
      {stored.error ? (
        <RecordsStatus loading={false} error={stored.error} onRetry={stored.refresh} />
      ) : !stored.data ? (
        <RecordsStatus loading error={null} onRetry={stored.refresh} />
      ) : (
        <DataGrid data={grid} edit={edit} />
      )}
      {more ? <p role="status" className={NOTE}>{more}</p> : null}
      {canWrite ? (
        <div data-sheet-bar="" className="sticky bottom-0 z-10 flex flex-wrap items-center gap-2 border-t border-border bg-card py-2">
          <Button variant="secondary" size="sm" icon="Plus" disabled={saving} onClick={() => setBuf((b) => addRow(b))}>
            Add row
          </Button>
          <Button size="sm" icon="Save" loading={saving} disabled={count === 0} onClick={save}>Save</Button>
          <span className={NOTE} data-sheet-count="">
            {count === 0 ? "No unsaved lines" : `${count} unsaved line${count === 1 ? "" : "s"} of at most ${MAX_SHEET_LINES}`}
          </span>
        </div>
      ) : null}
    </section>
  );
}
