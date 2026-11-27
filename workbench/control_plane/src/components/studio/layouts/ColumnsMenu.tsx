"use client";

/**
 * The Columns menu of a table workspace (STU-LY-2b). One button opens a menu with a checked item for each
 * column that may hide (every column but the first). A press toggles it. The choice lives in the URL
 * (`lib/studio/columnModel.ts`). It reuses `Button` and `ContextMenu`, and draws no colour of its own.
 */
import { useRef, useState } from "react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import Button from "@/components/ui/Button";

export interface ColumnsMenuProps {
  /** Every column of the table, in order, with its label. The first one never hides, so it is not listed. */
  columns: readonly { key: string; label: string }[];
  /** The keys that are hidden now. */
  hidden: readonly string[];
  onToggle: (key: string) => void;
  onShowAll: () => void;
}

export default function ColumnsMenu({ columns, hidden, onToggle, onShowAll }: ColumnsMenuProps) {
  const btn = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const off = new Set(hidden);
  // `ContextMenu` leaves the focus to its caller. Without this, a close drops the keyboard on the page body.
  const close = () => {
    setAt(null);
    btn.current?.querySelector<HTMLButtonElement>("button")?.focus();
  };
  const choices = columns.slice(1);
  if (choices.length === 0) return null;
  const items: CtxItem[] = [
    { kind: "label", label: "Show columns" },
    ...choices.map((c): CtxItem => ({ kind: "item", label: c.label, checked: !off.has(c.key), onSelect: () => onToggle(c.key) })),
    ...(hidden.length > 0 ? [{ kind: "sep" } as CtxItem, { kind: "item", label: "Show all columns", onSelect: onShowAll } as CtxItem] : []),
  ];
  return (
    <div ref={btn} data-columns-menu="" className="flex justify-end">
      <Button variant="secondary" size="sm" icon="Columns3" aria-haspopup="menu" aria-expanded={at !== null}
        onClick={() => { const r = btn.current?.getBoundingClientRect(); setAt({ x: r ? r.right - 200 : 0, y: r ? r.bottom + 4 : 0 }); }}>
        {hidden.length > 0 ? `Columns (${hidden.length} hidden)` : "Columns"}
      </Button>
      {at ? <ContextMenu x={at.x} y={at.y} items={items} autoFocus onClose={close} /> : null}
    </div>
  );
}
