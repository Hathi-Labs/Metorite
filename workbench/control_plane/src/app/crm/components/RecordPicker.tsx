"use client";

/**
 * The picker of a company or a contact (WS-53 CRM-U16, `crm_platform.md`
 * section 20.20).
 *
 * One control for the New deal form, the New contact form, the Company row of
 * a deal and "+ Add contact" on the Related tab. It is the lookup of the
 * field studio (`FieldInput`): an `Input` for the search and a `SelectButton`
 * for the hits. Both take the keyboard. It adds no hand-rolled control.
 *
 * The search is debounced and calls the list route of the target. With a
 * `companyId`, a contact search shows that company's contacts first. It also
 * lists them before the member types, and any other contact stays pickable.
 *
 * It only reads. The write belongs to the screen that holds the picker, and
 * that screen does not draw the picker in Mirror mode.
 */

import { Input } from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { useEffect, useState } from "react";
import { searchHits, type Pick, type PickerTarget } from "../lib/recordPicker";
import { LOOKUP_WAIT_MS, lookupOptions, pickLookup } from "./FieldInput";

export default function RecordPicker({
  target,
  label,
  value,
  onChange,
  companyId = null,
  companyName = null,
  disabled = false,
  id,
}: {
  target: PickerTarget;
  /** The name of the entity as the CRM calls it: "Company", "Contact". */
  label: string;
  value: Pick | null;
  onChange: (next: Pick | null) => void;
  /** Contacts only. Its contacts are listed first. */
  companyId?: string | null;
  /** The name of that company, for the hint under the search. */
  companyName?: string | null;
  disabled?: boolean;
  id?: string;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Pick[]>([]);
  const [failed, setFailed] = useState(false);
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
        })
        .catch(() => {
          if (!live) return;
          setFailed(true);
          setHits([]);
        });
    }, q ? LOOKUP_WAIT_MS : 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, target, narrow]);

  const shown = !query.trim() && !narrow ? [] : hits;

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Input
        id={id}
        inputSize="sm"
        icon="Search"
        aria-label={`Search for ${label.toLowerCase()}`}
        placeholder={`Search ${label.toLowerCase()}`}
        disabled={disabled}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <SelectButton
        label={label}
        value={value?.id ?? ""}
        options={lookupOptions(value, shown, failed)}
        onChange={(next) => onChange(pickLookup(next, shown, value))}
        prompt="—"
        widthClass="w-full"
        disabled={disabled}
      />
      {narrow && (
        <p className="text-[10px] text-muted-foreground">
          {companyName ? `Contacts at ${companyName} come first.` : "Contacts at this company come first."}
        </p>
      )}
    </div>
  );
}
