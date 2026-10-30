"use client";

/**
 * The picker of a company or a contact (WS-53 CRM-U16, `crm_platform.md`
 * section 20.20).
 *
 * One control for the New deal form, the New contact form, the Company row of
 * a deal and "Add contact" on the Related tab. It is ONE box: the member
 * types, the hits open in a list right under the box, and a pick closes it.
 * It is the search `Input` with the shared `OptionList` that another control
 * drives (the Knowledge editor uses it the same way). Nothing is hand-rolled.
 *
 * Keys, with the focus in the box: Down and Up move through the hits, Enter
 * picks the hit that is marked, and Escape closes the list. A pointer pick
 * keeps the focus in the box.
 *
 * The search is debounced and calls the list route of the target. With a
 * `companyId`, a contact search shows that company's contacts first. It also
 * lists them before the member types, and any other contact stays pickable.
 * When a value is held, the list ends with one "Clear" row. With no value it
 * has none.
 *
 * It only reads. The write belongs to the screen that holds the picker, and
 * that screen does not draw the picker in Mirror mode.
 */

import { Input } from "@/components/ui/Input";
import OptionList, { optionId, stepActive, type OptionRow } from "@/components/ui/OptionList";
import { useEffect, useId, useRef, useState } from "react";
import { searchHits, type Pick, type PickerTarget } from "../lib/recordPicker";
import { LOOKUP_WAIT_MS, SEARCH_FAILED } from "./FieldInput";

/** The id of the "Clear" row. No record has it. */
const CLEAR = "\u0000clear";

/** How tall the list may get. It stays small, so it opens below the box. */
const LIST_HEIGHT = 208;

/** The rows of the list: the hits, then "Clear" when a value is held. */
export function pickerRows(
  target: PickerTarget,
  hits: readonly Pick[],
  value: Pick | null,
): OptionRow[] {
  const icon = target === "organizations" ? "Building2" : "User";
  const rows: OptionRow[] = hits.map((h) => ({ id: h.id, label: h.name || h.id, icon }));
  if (value) rows.push({ id: CLEAR, label: "Clear", icon: "X" });
  return rows;
}

export default function RecordPicker({
  target,
  label,
  plural,
  value,
  onChange,
  companyId = null,
  companyName = null,
  disabled = false,
  autoFocus = false,
  id,
}: {
  target: PickerTarget;
  /** The name of the entity as the CRM calls it: "Company", "Contact". */
  label: string;
  /** Its plural, for the placeholder: "companies". Defaults to the label. */
  plural?: string;
  value: Pick | null;
  onChange: (next: Pick | null) => void;
  /** Contacts only. Its contacts are listed first. */
  companyId?: string | null;
  /** The name of that company, for the hint under the box. */
  companyName?: string | null;
  disabled?: boolean;
  /** Focus the box on mount, which opens the list. */
  autoFocus?: boolean;
  id?: string;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Pick[]>([]);
  const [failed, setFailed] = useState(false);
  /** The search that `hits` answers, so "No match" never shows while one runs. */
  const [settled, setSettled] = useState<string | null>(null);
  const [editing, setEditing] = useState(autoFocus);
  const [open, setOpen] = useState(autoFocus);
  const [active, setActive] = useState(-1);
  const listId = useId();
  const box = useRef<HTMLDivElement | null>(null);
  const narrow = target === "contacts" ? companyId : null;

  useEffect(() => {
    const q = query.trim();
    if (!q && !narrow) return;
    let live = true;
    // Typing waits for the last key. The first list of a company does not.
    const timer = setTimeout(() => {
      searchHits(target, q, narrow)
        .then((found) => {
          if (!live) return;
          setFailed(false);
          setHits(found);
          setSettled(q);
          setActive(-1);
        })
        .catch(() => {
          if (!live) return;
          setFailed(true);
          setHits([]);
          setSettled(q);
        });
    }, q ? LOOKUP_WAIT_MS : 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, target, narrow]);

  const typed = query.trim();
  // With no search and no company, nothing was asked, so no hit is shown.
  const shown = !typed && !narrow ? [] : hits;
  const rows = pickerRows(target, shown, value);
  const plain = (plural ?? label).toLowerCase();
  const noun = label.toLowerCase();
  const asked = typed ? settled === typed : narrow ? settled !== null : false;
  const status = failed ? SEARCH_FAILED : null;
  const hasList = rows.length > 0 || Boolean(status) || (Boolean(typed) && asked);
  const listOpen = open && !disabled && hasList;

  // Escape closes the list and nothing else. The dialog listens on the
  // document, so this listener sits on the window in the capture phase, and
  // only while the list is open. A second Escape then belongs to the dialog.
  useEffect(() => {
    if (!listOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [listOpen]);

  function pick(index: number) {
    const row = rows[index];
    if (!row) return;
    setOpen(false);
    setEditing(false);
    setQuery("");
    setActive(-1);
    onChange(row.id === CLEAR ? null : { id: row.id, name: row.label });
  }

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div ref={box}>
        <Input
          id={id}
          inputSize="sm"
          icon="Search"
          role="combobox"
          autoComplete="off"
          aria-label={`Search for ${noun}`}
          aria-autocomplete="list"
          aria-expanded={listOpen}
          aria-controls={listOpen ? listId : undefined}
          aria-activedescendant={listOpen && active >= 0 ? optionId(listId, active) : undefined}
          placeholder={`Search ${plain}…`}
          disabled={disabled}
          autoFocus={autoFocus}
          value={editing ? query : (value?.name ?? "")}
          onFocus={() => {
            setEditing(true);
            setOpen(true);
          }}
          onBlur={() => {
            setOpen(false);
            setEditing(false);
            setQuery("");
            setActive(-1);
          }}
          onClick={() => {
            setEditing(true);
            setOpen(true);
          }}
          onChange={(e) => {
            setEditing(true);
            setOpen(true);
            setActive(-1);
            setQuery(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              if (rows.length === 0) return;
              e.preventDefault();
              setEditing(true);
              if (!open) {
                setOpen(true);
                return;
              }
              const down = e.key === "ArrowDown";
              setActive((a) => (a < 0 ? (down ? 0 : rows.length - 1) : stepActive(a, down ? 1 : -1, rows.length)));
            } else if (e.key === "Enter" && listOpen && active >= 0) {
              e.preventDefault();
              e.stopPropagation();
              pick(active);
            }
          }}
        />
      </div>
      <OptionList
        anchor={box.current}
        open={listOpen}
        id={listId}
        label={label}
        rows={rows}
        active={active}
        onPick={pick}
        onHover={setActive}
        status={status}
        emptyText="No match."
        maxHeight={LIST_HEIGHT}
      />
      {narrow && !listOpen && (
        <p className="truncate text-[10px] text-muted-foreground">
          {companyName ? `Contacts at ${companyName} come first.` : "Contacts at this company come first."}
        </p>
      )}
    </div>
  );
}
